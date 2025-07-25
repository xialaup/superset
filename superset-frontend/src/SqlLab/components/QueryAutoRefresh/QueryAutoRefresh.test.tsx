/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { act } from 'react-dom/test-utils';
import { QueryState } from '@superset-ui/core';
import fetchMock from 'fetch-mock';
import configureStore from 'redux-mock-store';
import thunk from 'redux-thunk';
import { render, waitFor } from 'spec/helpers/testing-library';
import { cleanup } from '@testing-library/react';
import { LOG_ACTIONS_SQLLAB_FETCH_FAILED_QUERY } from 'src/logger/LogUtils';
import {
  CLEAR_INACTIVE_QUERIES,
  REFRESH_QUERIES,
} from 'src/SqlLab/actions/sqlLab';
import QueryAutoRefresh, {
  isQueryRunning,
  shouldCheckForQueries,
  QUERY_UPDATE_FREQ,
} from 'src/SqlLab/components/QueryAutoRefresh';
import { successfulQuery, runningQuery } from 'src/SqlLab/fixtures';
import { QueryDictionary } from 'src/SqlLab/types';
import mockDatabases from 'spec/fixtures/mockDatabases';

const middlewares = [thunk];
const mockStore = configureStore(middlewares);
const mockState = {
  databases: mockDatabases,
};

describe('QueryAutoRefresh', () => {
  const runningQueries: QueryDictionary = { [runningQuery.id]: runningQuery };
  const successfulQueries: QueryDictionary = {
    [successfulQuery.id]: successfulQuery,
  };
  const queriesLastUpdate = Date.now();
  const refreshApi = 'glob:*/api/v1/query/updated_since?*';

  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    fetchMock.reset();
    cleanup();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it('isQueryRunning returns true for valid running query', () => {
    expect(isQueryRunning(runningQuery)).toBe(true);
  });

  it('isQueryRunning returns false for valid not-running query', () => {
    expect(isQueryRunning(successfulQuery)).toBe(false);
  });

  it('isQueryRunning returns false for invalid query', () => {
    // @ts-ignore
    expect(isQueryRunning(null)).toBe(false);
    // @ts-ignore
    expect(isQueryRunning(undefined)).toBe(false);
    // @ts-ignore
    expect(isQueryRunning('I Should Be An Object')).toBe(false);
    // @ts-ignore
    expect(isQueryRunning({ state: { badFormat: true } })).toBe(false);
  });

  it('shouldCheckForQueries is true for valid running query', () => {
    expect(shouldCheckForQueries(runningQueries)).toBe(true);
  });

  it('shouldCheckForQueries is false for valid completed query', () => {
    expect(shouldCheckForQueries(successfulQueries)).toBe(false);
  });

  it('shouldCheckForQueries is false for invalid inputs', () => {
    // @ts-ignore
    expect(shouldCheckForQueries(null)).toBe(false);
    // @ts-ignore
    expect(shouldCheckForQueries(undefined)).toBe(false);
    expect(
      // @ts-ignore
      shouldCheckForQueries({
        // @ts-ignore
        '1234': null,
        // @ts-ignore
        '23425': 'hello world',
        // @ts-ignore
        '345': [],
        // @ts-ignore
        '57346': undefined,
      }),
    ).toBe(false);
  });

  it('Attempts to refresh when given pending query', async () => {
    const store = mockStore({ sqlLab: { ...mockState } });

    fetchMock.get(refreshApi, {
      result: [{ id: runningQuery.id, status: 'success' }],
    });

    render(
      <QueryAutoRefresh
        queries={runningQueries}
        queriesLastUpdate={queriesLastUpdate}
      />,
      { useRedux: true, store },
    );

    await act(async () => {
      jest.advanceTimersByTime(QUERY_UPDATE_FREQ + 100);
    });

    await waitFor(() =>
      expect(store.getActions()).toContainEqual(
        expect.objectContaining({ type: REFRESH_QUERIES }),
      ),
    );
  });

  it('Attempts to clear inactive queries when updated queries are empty', async () => {
    const store = mockStore({ sqlLab: { ...mockState } });

    fetchMock.get(refreshApi, { result: [] });

    render(
      <QueryAutoRefresh
        queries={runningQueries}
        queriesLastUpdate={queriesLastUpdate}
      />,
      { useRedux: true, store },
    );

    await act(async () => {
      jest.advanceTimersByTime(QUERY_UPDATE_FREQ + 100);
    });

    await waitFor(() =>
      expect(store.getActions()).toContainEqual(
        expect.objectContaining({ type: CLEAR_INACTIVE_QUERIES }),
      ),
    );

    expect(
      store.getActions().filter(({ type }) => type === REFRESH_QUERIES),
    ).toHaveLength(0);
    expect(fetchMock.calls(refreshApi)).toHaveLength(1);
  });

  it('Does not fail and attempts to refresh with mixed valid/invalid queries', async () => {
    const store = mockStore({ sqlLab: { ...mockState } });

    fetchMock.get(refreshApi, {
      result: [{ id: runningQuery.id, status: 'success' }],
    });

    render(
      <QueryAutoRefresh
        // @ts-ignore
        queries={{ ...runningQueries, g324t: null }}
        queriesLastUpdate={queriesLastUpdate}
      />,
      { useRedux: true, store },
    );

    await act(async () => {
      jest.advanceTimersByTime(QUERY_UPDATE_FREQ + 100);
    });

    await waitFor(() =>
      expect(store.getActions()).toContainEqual(
        expect.objectContaining({ type: REFRESH_QUERIES }),
      ),
    );
  });

  it('Does NOT Attempt to refresh when given only completed queries', async () => {
    const store = mockStore({ sqlLab: { ...mockState } });

    fetchMock.get(refreshApi, {
      result: [{ id: runningQuery.id, status: 'success' }],
    });

    render(
      <QueryAutoRefresh
        queries={successfulQueries}
        queriesLastUpdate={queriesLastUpdate}
      />,
      { useRedux: true, store },
    );

    await act(async () => {
      jest.advanceTimersByTime(QUERY_UPDATE_FREQ + 100);
    });

    await waitFor(() =>
      expect(store.getActions()).toContainEqual(
        expect.objectContaining({ type: CLEAR_INACTIVE_QUERIES }),
      ),
    );

    expect(fetchMock.calls(refreshApi)).toHaveLength(0);
  });

  it('logs the failed error for async queries', async () => {
    const store = mockStore({ sqlLab: { ...mockState } });

    fetchMock.get(refreshApi, {
      result: [
        {
          id: runningQuery.id,
          dbId: 1,
          state: QueryState.Failed,
          extra: {
            errors: [
              {
                error_type: 'TEST_ERROR',
                level: 'error',
                message: 'Syntax invalid',
                extra: {
                  issue_codes: [
                    {
                      code: 102,
                      message: 'DB failed',
                    },
                  ],
                },
              },
            ],
          },
        },
      ],
    });

    render(
      <QueryAutoRefresh
        queries={runningQueries}
        queriesLastUpdate={queriesLastUpdate}
      />,
      { useRedux: true, store },
    );

    await act(async () => {
      jest.advanceTimersByTime(QUERY_UPDATE_FREQ + 100);
    });

    await waitFor(() =>
      expect(store.getActions()).toContainEqual(
        expect.objectContaining({
          payload: expect.objectContaining({
            eventName: LOG_ACTIONS_SQLLAB_FETCH_FAILED_QUERY,
            eventData: expect.objectContaining({
              error_type: 'TEST_ERROR',
              error_details: 'Syntax invalid',
              issue_codes: [102],
            }),
          }),
        }),
      ),
    );
  });
});
