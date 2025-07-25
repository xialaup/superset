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
import { FunctionComponent, useState, useEffect, ChangeEvent } from 'react';

import { styled, t } from '@superset-ui/core';
import { useSingleViewResource } from 'src/views/CRUD/hooks';
import { extendedDayjs } from '@superset-ui/core/utils/dates';
import withToasts from 'src/components/MessageToasts/withToasts';
import {
  Input,
  JsonEditor,
  Modal,
  RangePicker,
} from '@superset-ui/core/components';
import { useJsonValidation } from '@superset-ui/core/components/AsyncAceEditor';

import { OnlyKeyWithType } from 'src/utils/types';
import { ModalTitleWithIcon } from 'src/components/ModalTitleWithIcon';
import { AnnotationObject } from './types';

interface AnnotationModalProps {
  addDangerToast: (msg: string) => void;
  addSuccessToast: (msg: string) => void;
  annotationLayerId: number;
  annotation?: AnnotationObject | null;
  onAnnotationAdd?: (annotation?: AnnotationObject) => void;
  onHide: () => void;
  show: boolean;
}

const StyledAnnotationTitle = styled.div`
  margin: ${({ theme }) => theme.sizeUnit * 2}px auto
    ${({ theme }) => theme.sizeUnit * 4}px auto;
`;

const StyledJsonEditor = styled(JsonEditor)`
  /* Border is already applied by AceEditor itself */
`;

const AnnotationContainer = styled.div`
  margin-bottom: ${({ theme }) => theme.sizeUnit * 5}px;

  .control-label {
    margin-bottom: ${({ theme }) => theme.sizeUnit * 2}px;
  }

  .required {
    margin-left: ${({ theme }) => theme.sizeUnit / 2}px;
    color: ${({ theme }) => theme.colorError};
  }

  input[type='text'] {
    width: 65%;
  }
`;

const AnnotationModal: FunctionComponent<AnnotationModalProps> = ({
  addDangerToast,
  addSuccessToast,
  annotationLayerId,
  annotation = null,
  onAnnotationAdd,
  onHide,
  show,
}) => {
  const [disableSave, setDisableSave] = useState<boolean>(true);
  const [currentAnnotation, setCurrentAnnotation] =
    useState<AnnotationObject | null>(null);
  const isEditMode = annotation !== null;

  // JSON validation for metadata
  const jsonAnnotations = useJsonValidation(currentAnnotation?.json_metadata, {
    errorPrefix: 'Invalid JSON metadata',
  });

  // annotation fetch logic
  const {
    state: { loading, resource },
    fetchResource,
    createResource,
    updateResource,
  } = useSingleViewResource<AnnotationObject>(
    `annotation_layer/${annotationLayerId}/annotation`,
    t('annotation'),
    addDangerToast,
  );

  const resetAnnotation = () => {
    // Reset annotation
    setCurrentAnnotation({
      short_descr: '',
      start_dttm: '',
      end_dttm: '',
      json_metadata: '',
      long_descr: '',
    });
  };

  const hide = () => {
    if (isEditMode) {
      setCurrentAnnotation(resource);
    } else {
      resetAnnotation();
    }
    onHide();
  };

  const onSave = () => {
    if (isEditMode) {
      // Edit
      if (currentAnnotation?.id) {
        const update_id = currentAnnotation.id;
        delete currentAnnotation.id;
        delete currentAnnotation.created_by;
        delete currentAnnotation.changed_by;
        delete currentAnnotation.changed_on_delta_humanized;
        delete currentAnnotation.layer;
        updateResource(update_id, currentAnnotation).then(response => {
          // No response on error
          if (!response) {
            return;
          }

          if (onAnnotationAdd) {
            onAnnotationAdd();
          }

          hide();

          addSuccessToast(t('The annotation has been updated'));
        });
      }
    } else if (currentAnnotation) {
      // Create
      createResource(currentAnnotation).then(response => {
        if (!response) {
          return;
        }

        if (onAnnotationAdd) {
          onAnnotationAdd();
        }

        hide();

        addSuccessToast(t('The annotation has been saved'));
      });
    }
  };

  const onAnnotationTextChange = (
    event: ChangeEvent<HTMLInputElement> | ChangeEvent<HTMLTextAreaElement>,
  ) => {
    const { target } = event;

    const data = {
      ...currentAnnotation,
      end_dttm: currentAnnotation ? currentAnnotation.end_dttm : '',
      short_descr: currentAnnotation ? currentAnnotation.short_descr : '',
      start_dttm: currentAnnotation ? currentAnnotation.start_dttm : '',
    };

    data[target.name as OnlyKeyWithType<typeof data, string>] = target.value;
    setCurrentAnnotation(data);
  };

  const onJsonChange = (json: string) => {
    const data = {
      ...currentAnnotation,
      end_dttm: currentAnnotation ? currentAnnotation.end_dttm : '',
      json_metadata: json,
      short_descr: currentAnnotation ? currentAnnotation.short_descr : '',
      start_dttm: currentAnnotation ? currentAnnotation.start_dttm : '',
    };
    setCurrentAnnotation(data);
  };

  const onDateChange = (dates: any, dateString: Array<string>) => {
    if (!dates?.[0] || !dates?.[1]) {
      const data = {
        ...currentAnnotation,
        start_dttm: '',
        end_dttm: '',
        short_descr: currentAnnotation?.short_descr ?? '',
      };
      setCurrentAnnotation(data);
      return;
    }

    const data = {
      ...currentAnnotation,
      start_dttm: dates[0].format('YYYY-MM-DD HH:mm'),
      end_dttm: dates[1].format('YYYY-MM-DD HH:mm'),
      short_descr: currentAnnotation?.short_descr ?? '',
    };
    setCurrentAnnotation(data);
  };

  const validate = () => {
    if (
      currentAnnotation?.short_descr?.length &&
      currentAnnotation?.start_dttm?.length &&
      currentAnnotation?.end_dttm?.length
    ) {
      setDisableSave(false);
    } else {
      setDisableSave(true);
    }
  };

  // Initialize
  useEffect(() => {
    if (
      isEditMode &&
      (!currentAnnotation?.id ||
        (annotation && annotation.id !== currentAnnotation.id) ||
        show)
    ) {
      if (annotation?.id !== null && !loading) {
        const id = annotation.id || 0;

        fetchResource(id);
      }
    } else if (
      !isEditMode &&
      (!currentAnnotation || currentAnnotation.id || show)
    ) {
      resetAnnotation();
    }
  }, [annotation]);

  useEffect(() => {
    if (resource) {
      setCurrentAnnotation(resource);
    }
  }, [resource]);

  // Validation
  useEffect(() => {
    validate();
  }, [
    currentAnnotation ? currentAnnotation.short_descr : '',
    currentAnnotation ? currentAnnotation.start_dttm : '',
    currentAnnotation ? currentAnnotation.end_dttm : '',
  ]);

  return (
    <Modal
      disablePrimaryButton={disableSave}
      onHandledPrimaryAction={onSave}
      onHide={hide}
      primaryButtonName={isEditMode ? t('Save') : t('Add')}
      show={show}
      width="55%"
      name={isEditMode ? t('Edit annotation') : t('Add annotation')}
      title={
        <ModalTitleWithIcon
          data-test="annotation-modal-title"
          isEditMode={isEditMode}
          title={isEditMode ? t('Edit annotation') : t('Add annotation')}
        />
      }
    >
      <StyledAnnotationTitle>
        <h4>{t('Basic information')}</h4>
      </StyledAnnotationTitle>
      <AnnotationContainer>
        <div className="control-label">
          {t('Name')}
          <span className="required">*</span>
        </div>
        <Input
          name="short_descr"
          onChange={onAnnotationTextChange}
          type="text"
          value={currentAnnotation?.short_descr}
        />
      </AnnotationContainer>
      <AnnotationContainer>
        <div className="control-label">
          {t('date')}
          <span className="required">*</span>
        </div>
        <RangePicker
          placeholder={[t('Start date'), t('End date')]}
          format="YYYY-MM-DD HH:mm"
          onCalendarChange={onDateChange}
          showTime={{ format: 'hh:mm a' }}
          use12Hours
          value={
            currentAnnotation?.start_dttm?.length ||
            currentAnnotation?.end_dttm?.length
              ? [
                  extendedDayjs(currentAnnotation.start_dttm),
                  extendedDayjs(currentAnnotation.end_dttm),
                ]
              : null
          }
        />
      </AnnotationContainer>
      <StyledAnnotationTitle>
        <h4>{t('Additional information')}</h4>
      </StyledAnnotationTitle>
      <AnnotationContainer>
        <div className="control-label">{t('description')}</div>
        <Input.TextArea
          name="long_descr"
          value={currentAnnotation ? currentAnnotation.long_descr : ''}
          placeholder={t('Description (this can be seen in the list)')}
          onChange={onAnnotationTextChange}
        />
      </AnnotationContainer>
      <AnnotationContainer>
        <div className="control-label">{t('JSON metadata')}</div>
        <StyledJsonEditor
          onChange={onJsonChange}
          value={
            currentAnnotation?.json_metadata
              ? currentAnnotation.json_metadata
              : ''
          }
          width="100%"
          height="120px"
          annotations={jsonAnnotations}
        />
      </AnnotationContainer>
    </Modal>
  );
};

export default withToasts(AnnotationModal);
