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
import { useEffect, useRef, useState } from 'react';
import { useDispatch } from 'react-redux';
import { useLocation } from 'react-router-dom';
import {
  getLabelsColorMap,
  isDefined,
  JsonObject,
  makeApi,
  LabelsColorMapSource,
  t,
  getClientErrorObject,
} from '@superset-ui/core';
import { Loading } from '@superset-ui/core/components';
import { addDangerToast } from 'src/components/MessageToasts/actions';
import { getUrlParam } from 'src/utils/urlUtils';
import { URL_PARAMS } from 'src/constants';
import getFormDataWithExtraFilters from 'src/dashboard/util/charts/getFormDataWithExtraFilters';
import { getAppliedFilterValues } from 'src/dashboard/util/activeDashboardFilters';
import { getParsedExploreURLParams } from 'src/explore/exploreUtils/getParsedExploreURLParams';
import { hydrateExplore } from 'src/explore/actions/hydrateExplore';
import ExploreViewContainer from 'src/explore/components/ExploreViewContainer';
import { ExploreResponsePayload, SaveActionType } from 'src/explore/types';
import { fallbackExploreInitialData } from 'src/explore/fixtures';
import { getItem, LocalStorageKeys } from 'src/utils/localStorageHelpers';
import { getFormDataWithDashboardContext } from 'src/explore/controlUtils/getFormDataWithDashboardContext';
import type Chart from 'src/types/Chart';

const isValidResult = (rv: JsonObject): boolean =>
  rv?.result?.form_data && rv?.result?.dataset;

const hasDatasetId = (rv: JsonObject): boolean =>
  isDefined(rv?.result?.dataset?.id);

const fetchExploreData = async (exploreUrlParams: URLSearchParams) => {
  const rv = await makeApi<{}, ExploreResponsePayload>({
    method: 'GET',
    endpoint: 'api/v1/explore/',
  })(exploreUrlParams);
  if (isValidResult(rv)) {
    if (hasDatasetId(rv)) {
      return rv;
    }
    // Since there's no dataset id but the API responded with a valid payload,
    // we assume the dataset was deleted, so we preserve some values from previous
    // state so if the user decide to swap the datasource, the chart config remains
    fallbackExploreInitialData.form_data = {
      ...rv.result.form_data,
      ...fallbackExploreInitialData.form_data,
    };
    if (rv.result?.slice) {
      fallbackExploreInitialData.slice = rv.result.slice;
    }
  }
  let message = t('Failed to load chart data');
  const responseError = rv?.result?.message;
  if (responseError) {
    message = `${message}:\n${responseError}`;
  }
  throw new Error(message);
};

const getDashboardPageContext = (pageId?: string | null) => {
  if (!pageId) {
    return null;
  }
  return getItem(LocalStorageKeys.DashboardExploreContext, {})[pageId] || null;
};

const getDashboardContextFormData = () => {
  const dashboardPageId = getUrlParam(URL_PARAMS.dashboardPageId);
  const dashboardContext = getDashboardPageContext(dashboardPageId);
  if (dashboardContext) {
    const sliceId = getUrlParam(URL_PARAMS.sliceId) || 0;
    const {
      colorScheme,
      labelsColor,
      labelsColorMap,
      sharedLabelsColors,
      chartConfiguration,
      nativeFilters,
      filterBoxFilters,
      dataMask,
      dashboardId,
      activeFilters,
    } = dashboardContext;

    const dashboardContextWithFilters = getFormDataWithExtraFilters({
      chart: { id: sliceId },
      filters: getAppliedFilterValues(sliceId, filterBoxFilters),
      nativeFilters,
      chartConfiguration,
      dataMask,
      colorScheme,
      labelsColor,
      labelsColorMap,
      sharedLabelsColors,
      sliceId,
      allSliceIds: [sliceId],
      extraControls: {},
      ...(activeFilters && { activeFilters }),
    });
    Object.assign(dashboardContextWithFilters, {
      dashboardId,
    });
    return dashboardContextWithFilters;
  }
  return null;
};

export default function ExplorePage() {
  const [isLoaded, setIsLoaded] = useState(false);
  const isExploreInitialized = useRef(false);
  const dispatch = useDispatch();
  const location = useLocation();

  useEffect(() => {
    const exploreUrlParams = getParsedExploreURLParams(location);
    const saveAction = getUrlParam(
      URL_PARAMS.saveAction,
    ) as SaveActionType | null;
    const dashboardContextFormData = getDashboardContextFormData();

    if (!isExploreInitialized.current || !!saveAction) {
      fetchExploreData(exploreUrlParams)
        .then(({ result }) => {
          const formData = dashboardContextFormData
            ? getFormDataWithDashboardContext(
                result.form_data,
                dashboardContextFormData,
                saveAction,
              )
            : result.form_data;

          dispatch(
            hydrateExplore({
              ...result,
              form_data: formData,
              saveAction,
            }),
          );
        })
        .catch(err => Promise.all([getClientErrorObject(err), err]))
        .then(resolved => {
          const [clientError, err] = resolved || [];
          if (!err) {
            return Promise.resolve();
          }
          const errorMesage =
            clientError?.message ||
            clientError?.error ||
            t('Failed to load chart data.');
          dispatch(addDangerToast(errorMesage));

          if (err.extra?.datasource) {
            const exploreData = {
              ...fallbackExploreInitialData,
              dataset: {
                ...fallbackExploreInitialData.dataset,
                id: err.extra?.datasource,
                name: err.extra?.datasource_name,
                extra: {
                  error: err,
                },
              },
            };
            const chartId = exploreUrlParams.get('slice_id');
            return (
              chartId
                ? makeApi<void, { result: Chart }>({
                    method: 'GET',
                    endpoint: `api/v1/chart/${chartId}`,
                  })()
                : Promise.reject()
            )
              .then(
                ({ result: { id, url, owners, form_data: _, ...data } }) => {
                  const slice = {
                    ...data,
                    datasource: err.extra?.datasource_name,
                    slice_id: id,
                    slice_url: url,
                    owners: owners?.map(({ id }) => id),
                  };
                  dispatch(
                    hydrateExplore({
                      ...exploreData,
                      slice,
                    }),
                  );
                },
              )
              .catch(() => {
                dispatch(hydrateExplore(exploreData));
              });
          }
          dispatch(hydrateExplore(fallbackExploreInitialData));
          return Promise.resolve();
        })
        .finally(() => {
          setIsLoaded(true);
          isExploreInitialized.current = true;
        });
    }
    getLabelsColorMap().source = LabelsColorMapSource.Explore;
  }, [dispatch, location]);

  if (!isLoaded) {
    return <Loading />;
  }
  return <ExploreViewContainer />;
}
