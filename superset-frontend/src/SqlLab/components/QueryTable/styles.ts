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
import { styled, css } from '@superset-ui/core';
import { IconTooltip } from '@superset-ui/core/components';

export const StaticPosition = css`
  position: static;
`;

export const verticalAlign = css`
  vertical-align: 0em;
  svg {
    height: 0.9em;
  }
`;

export const StyledTooltip = styled(IconTooltip)`
  padding-right: ${({ theme }) => theme.sizeUnit * 2}px;
  span {
    color: ${({ theme }) => theme.colors.grayscale.base};
    &: hover {
      color: ${({ theme }) => theme.colorPrimary};
    }
  }
`;
