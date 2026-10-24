import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import {
  createKycApplication,
  fetchKycApplications,
  submitKyc,
  uploadKycDocument,
} from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import { useToast } from '../hooks/useToast';
import { fileToBase64, formatDate, problemCode, problemMessage } from '../utils/format';

const bk = () => getBackend();
const MAX_DOCUMENT_BYTES = 5 * 1024 * 1024;

const startSchema = z.object({
  fullName: z.string().min(1, 'Full name is required').max(120, 'Max 120 characters'),
  country: z.string().regex(/^[A-Za-z]{2}$/, 'Use a 2-letter country code (e.g. US)'),
});

type StartValues = z.infer<typeof startSchema>;

export function KycPage() {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const applications = useQuery({ queryKey: ['kyc', bk()], queryFn: fetchKycApplications });
  const items = applications.data?.items ?? [];

  const current = (selectedId ? items.find((k) => k.id === selectedId) : null) ?? items[0] ?? null;

  const startForm = useForm<StartValues>({
    resolver: zodResolver(startSchema),
    defaultValues: { fullName: '', country: '' },
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['kyc'] });

  const startMutation = useMutation({
    mutationFn: (values: StartValues) =>
      createKycApplication({ fullName: values.fullName.trim(), country: values.country.toUpperCase() }),
    onSuccess: (app) => {
      invalidate();
      toastSuccess('KYC application created');
      setSelectedId(app.id);
      startForm.reset();
      setServerError(null);
    },
    onError: (e) => {
      const msg = problemMessage(e, 'Could not start the application.');
      setServerError(msg);
      toastError(msg);
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (input: { id: string; documentType: string; contentBase64: string }) =>
      uploadKycDocument(input.id, {
        documentType: input.documentType as 'passport' | 'national_id' | 'proof_of_address',
        contentBase64: input.contentBase64,
      }),
    onSuccess: () => {
      invalidate();
      toastSuccess('Document uploaded');
      setServerError(null);
    },
    onError: (e) => {
      const msg = problemCode(e) === 'document_too_large' ? 'Document is too large (max 5 MB).' : problemMessage(e, 'Upload failed.');
      setServerError(msg);
      toastError(msg);
    },
  });

  const submitMutation = useMutation({
    mutationFn: (id: string) => submitKyc(id),
    onSuccess: (app) => {
      invalidate();
      if (app.status === 'rejected') {
        toastError(`Application rejected: ${app.rejectionReason ?? 'sanctions screening'}`);
      } else {
        toastSuccess('Application submitted for review');
      }
      setServerError(null);
    },
    onError: (e) => {
      const msg = problemCode(e) === 'kyc_documents_required'
        ? 'At least one document must be uploaded before submitting.'
        : problemMessage(e, 'Submit failed.');
      setServerError(msg);
      toastError(msg);
    },
  });

  function onFileChosen(file: File | undefined, appId: string, documentType: string) {
    if (!file) return;
    if (file.size > MAX_DOCUMENT_BYTES) {
      setServerError('Document is too large (max 5 MB).');
      return;
    }
    fileToBase64(file)
      .then((contentBase64) => uploadMutation.mutate({ id: appId, documentType, contentBase64 }))
      .catch((e: unknown) => {
        setServerError(problemMessage(e, 'Could not read the file.'));
      });
  }

  const canEdit = current !== null && current.status === 'pending';

  return (
    <>
      <PageHeader
        eyebrow="COMPLIANCE"
        title="KYC"
        subtitle="Identity verification: create an application, upload documents, submit for review."
      />

      {serverError ? (
        <Alert severity="error" className="page-alert" onClose={() => setServerError(null)}>
          {serverError}
        </Alert>
      ) : null}

      <DataPage
        isLoading={applications.isLoading}
        isError={applications.isError}
        error={applications.error}
        onRetry={() => void applications.refetch()}
        isEmpty={items.length === 0}
        emptyTitle="No KYC application yet"
        emptyHint="Start an application below to verify your identity."
        emptyAction={<StartKycForm form={startForm} onSubmit={(v) => startMutation.mutate(v)} busy={startMutation.isPending} />}
        loading={<TableSkeleton rows={3} columns={4} />}
      >
        <div className="kyc-layout">
          <div className="kyc-main">
            {current ? (
              <section className="panel">
                <div className="panel-title-row">
                  <h3>Application {current.id.slice(0, 8)}…</h3>
                  <StatusChip status={current.status} size="medium" />
                </div>
                <dl className="fc-dl">
                  <div>
                    <dt>Full name</dt>
                    <dd className="fc-strong">{current.fullName}</dd>
                  </div>
                  <div>
                    <dt>Country</dt>
                    <dd>{current.country}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatDate(current.createdAt)}</dd>
                  </div>
                  <div>
                    <dt>Submitted</dt>
                    <dd>{formatDate(current.submittedAt)}</dd>
                  </div>
                  <div>
                    <dt>Decided</dt>
                    <dd>{formatDate(current.decidedAt)}</dd>
                  </div>
                </dl>
                {current.status === 'rejected' && current.rejectionReason ? (
                  <Alert severity="error" sx={{ mt: 2 }}>
                    <strong>Rejected:</strong> {current.rejectionReason}
                  </Alert>
                ) : null}

                <h4 className="panel-subheading">Documents</h4>
                {current.documents.length === 0 ? (
                  <p className="panel-muted">No documents uploaded yet. At least one document is required before submitting.</p>
                ) : (
                  <table className="fc-table">
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Uploaded</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.documents.map((doc) => (
                        <tr key={doc.id}>
                          <td>{doc.documentType.replaceAll('_', ' ')}</td>
                          <td>
                            <StatusChip status={doc.status} />
                          </td>
                          <td className="fc-cell-muted">{formatDate(doc.uploadedAt)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {canEdit ? (
                  <div className="kyc-upload-row">
                    <DocumentUpload
                      disabled={uploadMutation.isPending || submitMutation.isPending}
                      onUpload={(documentType, file) => onFileChosen(file, current.id, documentType)}
                    />
                    <Button
                      variant="contained"
                      disabled={submitMutation.isPending || current.documents.length === 0}
                      onClick={() => submitMutation.mutate(current.id)}
                    >
                      {submitMutation.isPending ? 'Submitting…' : 'Submit for review'}
                    </Button>
                  </div>
                ) : (
                  <Alert severity="info" sx={{ mt: 2 }}>
                    This application is {current.status.replaceAll('_', ' ')} — documents can no longer be changed.
                  </Alert>
                )}
              </section>
            ) : null}
          </div>

          <div className="kyc-side">
            {items.length > 0 ? (
              <section className="panel">
                <h3>History</h3>
                <ul className="fc-list">
                  {items.map((app) => (
                    <li key={app.id}>
                      <button
                        className={`fc-list-item${current?.id === app.id ? ' selected' : ''}`}
                        onClick={() => setSelectedId(app.id)}
                      >
                        <span className="fc-strong">{app.fullName}</span>
                        <span className="fc-cell-muted">
                          {app.country} · {formatDate(app.createdAt)}
                        </span>
                        <StatusChip status={app.status} />
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="kyc-new-wrap">
                  <StartKycForm form={startForm} onSubmit={(v) => startMutation.mutate(v)} busy={startMutation.isPending} compact />
                </div>
              </section>
            ) : null}
          </div>
        </div>
      </DataPage>
    </>
  );
}

function StartKycForm({
  form,
  onSubmit,
  busy,
  compact = false,
}: {
  form: ReturnType<typeof useForm<StartValues>>;
  onSubmit: (values: StartValues) => void;
  busy: boolean;
  compact?: boolean;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = form;
  return (
    <form
      className={compact ? 'kyc-start-compact' : 'kyc-start'}
      onSubmit={handleSubmit(onSubmit)}
      noValidate
    >
      {!compact ? <h3>Start KYC</h3> : null}
      <TextField
        label="Full legal name"
        fullWidth
        error={Boolean(errors.fullName)}
        helperText={errors.fullName?.message}
        {...register('fullName')}
      />
      <TextField
        label="Country code"
        fullWidth
        placeholder="US"
        inputProps={{ maxLength: 2, style: { textTransform: 'uppercase' } }}
        error={Boolean(errors.country)}
        helperText={errors.country?.message ?? 'ISO 3166-1 alpha-2, e.g. US, DE, GB'}
        {...register('country', { setValueAs: (v: string) => v.toUpperCase().trim() })}
      />
      <Button type="submit" variant="contained" disabled={busy}>
        {busy ? 'Creating…' : compact ? 'New application' : 'Start application'}
      </Button>
    </form>
  );
}

function DocumentUpload({
  disabled,
  onUpload,
}: {
  disabled: boolean;
  onUpload: (documentType: string, file: File | undefined) => void;
}) {
  const [documentType, setDocumentType] = useState('passport');
  const fileRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="kyc-upload">
      <TextField
        select
        label="Document type"
        size="small"
        value={documentType}
        onChange={(e) => setDocumentType(e.target.value)}
        SelectProps={{ native: true }}
        sx={{ minWidth: 190 }}
      >
        <option value="passport">Passport</option>
        <option value="national_id">National ID</option>
        <option value="proof_of_address">Proof of address</option>
      </TextField>
      <Button variant="outlined" disabled={disabled} onClick={() => fileRef.current?.click()}>
        Upload document (max 5 MB)
      </Button>
      <input
        ref={fileRef}
        type="file"
        accept="image/*,.pdf"
        hidden
        onChange={(e) => {
          onUpload(documentType, e.target.files?.[0] ?? undefined);
          if (fileRef.current) fileRef.current.value = '';
        }}
      />
    </div>
  );
}

export default KycPage;
