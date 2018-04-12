# -*- coding: utf-8 -*-
#
# Copyright 2017 Capital One Services, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Compare two Pandas DataFrames

Originally this package was meant to provide similar functionality to
PROC COMPARE in SAS - i.e. human-readable reporting on the difference between
two dataframes.
"""

import os
import logging
import pandas as pd
import numpy as np

from datacompy import utils

LOG = logging.getLogger(__name__)

class Compare(object):
    """Comparison class to be used to compare whether two dataframes as equal.

    Both df1 and df2 should be dataframes containing all of the join_columns,
    with unique column names. Differences between values are compared to
    abs_tol + rel_tol * abs(df2['value']).

    Parameters
    ----------
    df1 : pandas ``DataFrame``
        First dataframe to check
    df2 : pandas ``DataFrame``
        Second dataframe to check
    join_columns : list or str, optional
        Column(s) to join dataframes on.  If a string is passed in, that one
        column will be used.
    on_index : bool, optional
        If True, the index will be used to join the two dataframes.  If both
        ``join_columns`` and ``on_index`` are provided, an exception will be
        raised.
    abs_tol : float, optional
        Absolute tolerance between two values.
    rel_tol : float, optional
        Relative tolerance between two values.
    df1_name : str, optional
        A string name for the first dataframe.  This allows the reporting to
        print out an actual name instead of "df1", and allows human users to
        more easily track the dataframes.
    df2_name : str, optional
        A string name for the second dataframe

    Attributes
    ----------
    df1_unq_rows : pandas ``DataFrame``
        All records that are only in df1 (based on a join on join_columns)
    df2_unq_rows : pandas ``DataFrame``
        All records that are only in df2 (based on a join on join_columns)
    """

    def __init__(
        self, df1, df2, join_columns=None, on_index=False, abs_tol=0,
        rel_tol=0, df1_name='df1', df2_name='df2'):

        if on_index and join_columns is not None:
            raise Exception('Only provide on_index or join_columns')
        elif on_index:
            self.on_index = True
            self.join_columns = []
        elif isinstance(join_columns, str):
            self.join_columns = [join_columns.lower()]
            self.on_index = False
        else:
            self.join_columns = [col.lower() for col in join_columns]
            self.on_index = False

        self._any_dupes = False
        self.df1 = df1
        self.df2 = df2
        self.df1_name = df1_name
        self.df2_name = df2_name
        self.abs_tol = abs_tol
        self.rel_tol = rel_tol
        self.df1_unq_rows = self.df2_unq_rows = self.intersect_rows = None
        self.column_stats = []
        self._compare()

    @property
    def df1(self):
        return self._df1

    @df1.setter
    def df1(self, df1):
        """Check that it is a dataframe and has the join columns"""
        self._df1 = df1
        self._validate_dataframe('df1')


    @property
    def df2(self):
        return self._df2

    @df2.setter
    def df2(self, df2):
        """Check that it is a dataframe and has the join columns"""
        self._df2 = df2
        self._validate_dataframe('df2')

    def _validate_dataframe(self, index):
        """Check that it is a dataframe and has the join columns

        Parameters
        ----------
        index : str
            The "index" of the dataframe - df1 or df2.
        """
        dataframe = getattr(self, index)
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError('{} must be a pandas DataFrame'.format(index))

        dataframe.columns = [col.lower() for col in dataframe.columns]
        #Check if join_columns are present in the dataframe
        if not set(self.join_columns).issubset(set(dataframe.columns)):
            raise ValueError('{} must have all columns from join_columns'.format(index))

        if len(set(dataframe.columns)) < len(dataframe.columns):
            raise ValueError('{} must have unique column names'.format(index))

        if self.on_index:
            if dataframe.index.duplicated().sum() > 0:
                self._any_dupes = True
        else:
            if len(dataframe.drop_duplicates(subset=self.join_columns)) < len(dataframe):
                self._any_dupes = True

    def _compare(self):
        """Actually run the comparison.  This tries to run df1.equals(df2)
        first so that if they're truly equal we can tell.

        This method will log out information about what is different between
        the two dataframes, and will also return a boolean.
        """
        LOG.debug('Checking equality')
        if self.df1.equals(self.df2):
            LOG.info('df1 Pandas.DataFrame.equals df2')
        else:
            LOG.info('df1 does not Pandas.DataFrame.equals df2')
        LOG.info('Number of columns in common: {0}'.format(
            len(self.intersect_columns())))
        LOG.debug('Checking column overlap')
        for col in self.df1_unq_columns():
            LOG.info('Column in df1 and not in df2: {0}'.format(col))
        LOG.info('Number of columns in df1 and not in df2: {0}'.format(
            len(self.df1_unq_columns())))
        for col in self.df2_unq_columns():
            LOG.info('Column in df2 and not in df1: {}'.format(col))
        LOG.info('Number of columns in df2 and not in df1: {}'.format(
            len(self.df2_unq_columns())))
        LOG.debug('Merging dataframes')
        self._dataframe_merge()
        self._intersect_compare()
        if self.matches():
            LOG.info('df1 matches df2')
        else:
            LOG.info('df1 does not match df2')

    def df1_unq_columns(self):
        """Get columns that are unique to df1"""
        return set(self.df1.columns) - set(self.df2.columns)

    def df2_unq_columns(self):
        """Get columns that are unique to df2"""
        return set(self.df2.columns) - set(self.df1.columns)

    def intersect_columns(self):
        """Get columns that are shared between the two dataframes"""
        return set(self.df1.columns) & set(self.df2.columns)

    def _dataframe_merge(self):
        """Merge df1 to df2 on the join columns, to get df1 - df2, df2 - df1
        and df1 & df2

        If ``on_index`` is True, this will join on index values, otherwise it
        will join on the ``join_columns``.
        """

        LOG.debug('Outer joining')
        if self._any_dupes:
            LOG.debug(
                'Duplicate rows found, deduping by order of remaining fields')
            #Bring index into a column
            if self.on_index:
                index_column = utils.temp_column_name(self.df1, self.df2)
                self.df1[index_column] = self.df1.index
                self.df2[index_column] = self.df2.index
                temp_join_columns = [index_column]
            else:
                temp_join_columns = list(self.join_columns)

            #Create order column for uniqueness of match
            order_column = utils.temp_column_name(self.df1, self.df2)
            self.df1[order_column] = self.df1.sort_values(
                by=list(self.df1.columns)).groupby(temp_join_columns).cumcount()
            self.df2[order_column] = self.df2.sort_values(
                by=list(self.df2.columns)).groupby(temp_join_columns).cumcount()
            temp_join_columns.append(order_column)

            params = {'on': temp_join_columns}
        elif self.on_index:
            params = {'left_index': True, 'right_index': True}
        else:
            params = {'on': self.join_columns}

        outer_join = self.df1.merge(
            self.df2,
            how='outer',
            suffixes=('_df1', '_df2'),
            indicator=True,
            **params)

        #Clean up temp columns for duplicate row matching
        if self._any_dupes:
            if self.on_index:
                outer_join.index = outer_join[index_column]
                outer_join.drop(index_column, axis=1, inplace=True)
                self.df1.drop(index_column, axis=1, inplace=True)
                self.df2.drop(index_column, axis=1, inplace=True)
            outer_join.drop(order_column, axis=1, inplace=True)
            self.df1.drop(order_column, axis=1, inplace=True)
            self.df2.drop(order_column, axis=1, inplace=True)

        df1_cols = utils.get_merged_columns(self.df1, outer_join, '_df1')
        df2_cols = utils.get_merged_columns(self.df2, outer_join, '_df2')

        LOG.debug('Selecting df1 unique rows')
        self.df1_unq_rows = (
            outer_join[outer_join['_merge'] == 'left_only'][df1_cols].copy())
        self.df1_unq_rows.columns = self.df1.columns

        LOG.debug('Selecting df2 unique rows')
        self.df2_unq_rows = (
            outer_join[outer_join['_merge'] == 'right_only'][df2_cols].copy())
        self.df2_unq_rows.columns = self.df2.columns
        LOG.info('Number of rows in df1 and not in df2: {}'.format(
            len(self.df1_unq_rows)))
        LOG.info('Number of rows in df2 and not in df1: {}'.format(
            len(self.df2_unq_rows)))

        LOG.debug('Selecting intersecting rows')
        self.intersect_rows = outer_join[outer_join['_merge'] == 'both'].copy()
        LOG.info(
            'Number of rows in df1 and df2 (not necessarily equal): {}'.format(
                len(self.intersect_rows)))

    def _intersect_compare(self):
        """Run the comparison on the intersect dataframe

        This loops through all columns that are shared between df1 and df2, and
        creates a column column_match which is True for matches, False
        otherwise.
        """
        LOG.debug('Comparing intersection')
        row_cnt = len(self.intersect_rows)
        for column in self.intersect_columns():
            if column in self.join_columns:
                match_cnt = row_cnt
                col_match = ''
                max_diff = 0
                null_diff = 0
            else:
                col_1 = column + '_df1'
                col_2 = column + '_df2'
                col_match = column + '_match'
                self.intersect_rows[col_match] = utils.columns_equal(
                    self.intersect_rows[col_1],
                    self.intersect_rows[col_2],
                    self.rel_tol,
                    self.abs_tol)
                match_cnt = self.intersect_rows[col_match].sum()

                try:
                    max_diff = (
                        self.intersect_rows[col_1]
                        - self.intersect_rows[col_2]).abs().max()
                except:
                    max_diff = 0

                null_diff = (
                    (self.intersect_rows[col_1].isnull())
                    ^ (self.intersect_rows[col_2].isnull())).sum()

            if row_cnt > 0:
                match_rate = float(match_cnt) / row_cnt
            else:
                match_rate = 0
            LOG.info('{0}: {1} / {2} ({3:.2%}) match'.format(
                column, match_cnt, row_cnt, match_rate))

            self.column_stats.append({
                'column': column,
                'match_column': col_match,
                'match_cnt': match_cnt,
                'unequal_cnt': row_cnt - match_cnt,
                'dtype1': str(self.df1[column].dtype),
                'dtype2': str(self.df2[column].dtype),
                'all_match': all((
                    self.df1[column].dtype == self.df2[column].dtype,
                    row_cnt == match_cnt)),
                'max_diff': max_diff,
                'null_diff': null_diff
                })

    def all_columns_match(self):
        """Whether the columns all match in the dataframes"""
        return self.df1_unq_columns() == self.df2_unq_columns() == set()

    def all_rows_overlap(self):
        """Whether the rows are all present in both dataframes

        Returns
        -------
        bool
            True if all rows in df1 are in df2 and vice versa (based on
            existence for join option)
        """
        return len(self.df1_unq_rows) == len(self.df2_unq_rows) == 0


    def count_matching_rows(self):
        """Count the number of rows match (on overlapping fields)

        Returns
        -------
        int
            Number of matching rows
        """
        match_columns = []
        for column in self.intersect_columns():
            if column not in self.join_columns:
                match_columns.append(column + '_match')
        return self.intersect_rows[match_columns].all(axis=1).sum()

    def intersect_rows_match(self):
        """Check whether the intersect rows all match"""
        actual_length = self.intersect_rows.shape[0]
        return self.count_matching_rows() == actual_length

    def matches(self, ignore_extra_columns=False):
        """Return True or False if the dataframes match.

        Parameters
        ----------
        ignore_extra_columns : bool
            Ignores any columns in one dataframe and not in the other.
        """
        if not ignore_extra_columns and not self.all_columns_match():
            return False
        elif not self.all_rows_overlap():
            return False
        elif not self.intersect_rows_match():
            return False
        else:
            return True

    def subset(self):
        """Return True if dataframe 2 is a subset of dataframe 1.

        Dataframe 2 is considered a subset if all of its columns are in
        dataframe 1, and all of its rows match rows in dataframe 1 for the
        shared columns.
        """
        if not self.df2_unq_columns() == set():
            return False
        elif not len(self.df2_unq_rows) == 0:
            return False
        elif not self.intersect_rows_match():
            return False
        else:
            return True


    def sample_mismatch(self, column, sample_count=10, for_display=False):
        """Returns a sample sub-dataframe which contains the identifying
        columns, and df1 and df2 versions of the column.

        Parameters
        ----------
        column : str
            The raw column name (i.e. without ``_df1`` appended)
        sample_count : int, optional
            The number of sample records to return.  Defaults to 10.
        for_display : bool, optional
            Whether this is just going to be used for display (overwrite the
            column names)

        Returns
        -------
        Pandas.DataFrame
            A sample of the intersection dataframe, containing only the
            "pertinent" columns, for rows that don't match on the provided
            column.
        """
        row_cnt = self.intersect_rows.shape[0]
        col_match = self.intersect_rows[column + '_match']
        match_cnt = col_match.sum()
        sample_count = min(sample_count, row_cnt - match_cnt)
        sample = self.intersect_rows[~col_match].sample(sample_count)
        return_cols = self.join_columns + [column + '_df1', column + '_df2']
        to_return = sample[return_cols]
        if for_display:
            to_return.columns = (
                self.join_columns +
                [column + ' (' + self.df1_name + ')',
                column + ' (' + self.df2_name + ')'])
        return to_return


    def _report_header(self):
        return utils.render('header.txt')

    def _report_dataframe_summary(self):
        return utils.render('dataframe_summary.txt', **{'dataframe_summary': pd.DataFrame({
            'DataFrame': [self.df1_name, self.df2_name],
            'Columns': [self.df1.shape[1], self.df2.shape[1]],
            'Rows': [self.df1.shape[0], self.df2.shape[0]]})})

    def _report_column_summary(self):
        return utils.render(
            'column_summary.txt', **{
                'number_in_common': len(self.intersect_columns()),
                'df1_unq_col_count': len(self.df1_unq_columns()),
                'df2_unq_col_count': len(self.df2_unq_columns()),
                'df1_name': self.df1_name,
                'df2_name': self.df2_name})

    def _report_row_summary(self):
        matched_on = 'index' if self.on_index else ', '.join(self.join_columns)
        return utils.render(
            'row_summary.txt', **{
                'matched_on': matched_on,
                'abs_tol': self.abs_tol,
                'rel_tol': self.rel_tol,
                'rows_in_common': self.intersect_rows.shape[0],
                'df1_unq_row_count': self.df1_unq_rows.shape[0],
                'df2_unq_row_count': self.df2_unq_rows.shape[0],
                'unequal_count': self.intersect_rows.shape[0] - self.count_matching_rows(),
                'equal_count': self.count_matching_rows(),
                'df1_name': self.df1_name,
                'df2_name': self.df2_name,
                'any_dupes': 'Yes' if self._any_dupes else 'No'})

    def _report_column_comparison(self):
        return utils.render(
            'column_comparison.txt', **{
                'unequal_col_count':
                    len([col for col in self.column_stats if col['unequal_cnt'] > 0]),
                'equal_col_count':
                    len([col for col in self.column_stats if col['unequal_cnt'] == 0]),
                'unequal_values': sum([col['unequal_cnt'] for col in self.column_stats])})

    def _report_mismatches(self, sample_count):
        cnt_intersect = self.intersect_rows.shape[0]
        match_stats = []
        match_sample = []
        any_mismatch = False
        headers = [
            'Column', '{} dtype'.format(self.df1_name), '{} dtype'.format(self.df2_name),
            '# Unequal', 'Max Diff', '# Null Diff']
        for column in self.column_stats:
            if not column['all_match']:
                any_mismatch = True
                match_stats.append(dict(zip(headers, [
                    column['column'],
                    column['dtype1'],
                    column['dtype2'],
                    column['unequal_cnt'],
                    column['max_diff'],
                    column['null_diff']
                    ])))
                if column['unequal_cnt'] > 0:
                    match_sample.append(self.sample_mismatch(
                        column['column'], sample_count, for_display=True))

        if any_mismatch:
            df_match_stats = pd.DataFrame(match_stats)
            df_match_stats.sort_values('Column', inplace=True)
            sample_rows = '\n\n'.join(map(lambda x: x.to_string(), match_sample))
            return utils.render('mismatches.txt', **{
                'df_match_stats': df_match_stats[headers], # For column order
                'sample_rows': sample_rows
                })
        return ''

    def _report_unique_rows(self, index, sample_count):
        dataframe = getattr(self, '{index}_unq_rows'.format(index=index))
        df_name = getattr(self, '{index}_name'.format(index=index))
        if dataframe.shape[0] > 0:
            columns = dataframe.columns[:10]
            unq_count = min(sample_count, dataframe.shape[0])
            return utils.render('unique_rows.txt', **{
                'df_name': df_name,
                'df_name_dashes': '-' * len(df_name),
                'sample_rows': dataframe.sample(unq_count)[columns]
                })
        return ''

    def report(self, sample_count=10):
        """Returns a string representation of a report.  The representation can
        then be printed or saved to a file.

        Parameters
        ----------
        sample_count : int, optional
            The number of sample records to return.  Defaults to 10.

        Returns
        -------
        str
            The report, formatted as a human-readable string
        """
        report_sections = [self._report_header()]
        report_sections.append(self._report_dataframe_summary())
        report_sections.append(self._report_column_summary())
        report_sections.append(self._report_row_summary())
        report_sections.append(self._report_column_comparison())
        report_sections.append(self._report_mismatches(sample_count))
        report_sections.append(self._report_unique_rows('df1', sample_count))
        report_sections.append(self._report_unique_rows('df2', sample_count))

        return '\n\n'.join(filter(lambda x: x.strip(), report_sections))
