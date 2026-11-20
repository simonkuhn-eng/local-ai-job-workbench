import type {
  AdaptBlocksResponse,
  AiBatchStatus,
  AutofillAddressResult,
  CompaniesOverviewResponse,
  CompanyHistoryEntry,
  CoverLetter,
  CvData,
  CvVariant,
  InterviewPrepRecord,
  JobDetail,
  JobSummary,
  LetterParagraphItem,
  LetterPreflight,
  OverviewResponse,
  ParagraphRecommendResponse,
  PipelineSankeyResponse,
  PrioritizedAiRanking,
  PriorityFocus,
  ScanLiveStatus,
  SkillGapHeatmapResponse,
} from './types';

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const errBody = await res.json();
      if (errBody?.detail) detail = String(errBody.detail);
    } catch {
      // keep fallback status
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getOverview: (refreshAi = false) =>
    requestJson<OverviewResponse>(`/api/overview${refreshAi ? '?refresh_ai=true' : ''}`),

  listJobs: (params: {
    lifecycle?: string;
    q?: string;
    tier?: string;
    city_area?: string;
    status?: string;
    min_score?: number;
    include_excluded?: boolean;
    hide_prioritized?: boolean;
    hide_applied?: boolean;
    sort_by?: string;
    limit?: number;
  }) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== '') sp.set(k, String(v));
    });
    return requestJson<{ items: JobSummary[]; total_matching: number }>(`/api/jobs?${sp.toString()}`);
  },

  getJobDetail: (jobId: number) => requestJson<JobDetail>(`/api/jobs/${jobId}`),

  patchJob: (jobId: number, changes: Record<string, unknown>, confirmedSent = false) =>
    requestJson<{ updated: boolean; event_id: number | null; job: JobDetail }>(`/api/jobs/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify({ changes, confirmed_sent: confirmedSent }),
    }),

  undoJobEvent: (eventId: number) =>
    requestJson<{ updated: boolean }>(`/api/jobs/undo/${eventId}`, { method: 'POST' }),

  setCompanyPreference: (company: string, preference: 'preferred' | 'neutral' | 'excluded') =>
    requestJson<{ company: string; preference: string }>('/api/companies/preference', {
      method: 'POST',
      body: JSON.stringify({ company, preference }),
    }),

  startScan: (onlyCompanies?: string[]) =>
    requestJson<{ started: boolean; status: ScanLiveStatus }>('/api/scan/start', {
      method: 'POST',
      body: JSON.stringify({ only_companies: onlyCompanies || null, max_workers: 10 }),
    }),

  getScanStatus: () => requestJson<ScanLiveStatus>('/api/scan/status'),

  assessJob: (jobId: number, model: string, deep = false) =>
    requestJson<{ job_id: number; ai_score: number; model: string; job: JobDetail }>(
      `/api/jobs/${jobId}/assess`,
      { method: 'POST', body: JSON.stringify({ model, deep }) }
    ),

  startBatchAssess: (limit: number, model: string, tier: string) =>
    requestJson<AiBatchStatus>('/api/ai/batch-assess', {
      method: 'POST',
      body: JSON.stringify({ limit, model, tier }),
    }),

  cancelBatchAssess: () =>
    requestJson<{ cancelling: boolean; running: boolean }>('/api/ai/batch-cancel', {
      method: 'POST',
    }),

  getBatchStatus: () => requestJson<AiBatchStatus>('/api/ai/batch-status'),

  listCvVariants: () => requestJson<CvVariant[]>('/api/cv/variants'),

  getCvVariant: (variantId: number) => requestJson<CvVariant>(`/api/cv/variants/${variantId}`),

  createCvVariant: (
    name: string,
    jobId: number | null,
    lang = 'de',
    baseVariantId = 1,
    preset?: 'ai_backend' | 'robotics_perception'
  ) =>
    requestJson<CvVariant>('/api/cv/variants', {
      method: 'POST',
      body: JSON.stringify({ name, job_id: jobId, base_variant_id: baseVariantId, lang, preset }),
    }),

  renameCvVariant: (variantId: number, name: string) =>
    requestJson<CvVariant>(`/api/cv/variants/${variantId}/rename`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  deleteCvVariant: (variantId: number) =>
    requestJson<{ deleted: boolean }>(`/api/cv/variants/${variantId}`, {
      method: 'DELETE',
    }),

  saveCvVariant: (variantId: number, cvData: CvData, name?: string, lang = 'de', compilePdf = true) =>
    requestJson<CvVariant>(`/api/cv/variants/${variantId}`, {
      method: 'PUT',
      body: JSON.stringify({ name, lang, cv_data: cvData, compile_pdf: compilePdf }),
    }),

  adjustCvVariant: (
    variantId: number,
    prompt: string,
    model: string,
    includeJobContext: boolean,
    jobId: number | null
  ) =>
    requestJson<{ summary: string; model: string; warnings: string[]; variant: CvVariant }>(
      `/api/cv/variants/${variantId}/adjust`,
      {
        method: 'POST',
        body: JSON.stringify({
          prompt,
          model,
          include_job_context: includeJobContext,
          job_id: jobId,
        }),
      }
    ),

  undoCvVariant: (variantId: number) =>
    requestJson<CvVariant>(`/api/cv/variants/${variantId}/undo`, { method: 'POST' }),

  listLetters: () => requestJson<CoverLetter[]>('/api/letters'),

  getLetter: (letterId: number) => requestJson<CoverLetter>(`/api/letters/${letterId}`),

  prefillLetter: (jobId: number | null, lang = 'de') =>
    requestJson<CoverLetter>('/api/letters/prefill', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, lang }),
    }),

  saveLetterDraft: (letter: Partial<CoverLetter>) =>
    requestJson<CoverLetter>('/api/letters/draft', {
      method: 'POST',
      body: JSON.stringify({ ...letter, letter_id: letter.id }),
    }),

  compileLetter: (letter: CoverLetter) =>
    requestJson<CoverLetter>('/api/letters/compile', {
      method: 'POST',
      body: JSON.stringify({ ...letter, letter_id: letter.id }),
    }),

  generateLetter: (params: {
    job_id: number | null;
    lang: 'de' | 'en';
    model: 'hydrogen' | 'argon' | 'neon';
    instruction: string;
    current_draft: Partial<CoverLetter>;
    proofread_only?: boolean;
    humanize_only?: boolean;
  }) =>
    requestJson<{
      subject: string;
      salutation: string;
      contact_person: string;
      body_text: string;
      closing: string;
      summary: string;
      removed_patterns?: string[];
      audit_warnings?: string[];
      model: string;
    }>('/api/letters/generate', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  autofillAddress: (params: {
    job_id?: number | null;
    company?: string;
    location?: string;
    title?: string;
    lang?: 'de' | 'en';
  }) =>
    requestJson<AutofillAddressResult>('/api/letters/autofill-address', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  deleteLetter: (letterId: number) =>
    requestJson<{ deleted: boolean }>(`/api/letters/${letterId}`, { method: 'DELETE' }),

  exportDossier: (
    jobId: number,
    lang: 'auto' | 'de' | 'en' = 'auto',
    includeAttachments = true,
    openExplorer = false
  ) =>
    requestJson<{
      folder: string;
      lang: string;
      files: string[];
      file_count: number;
      warnings?: string[];
      bundle_size_mb?: number | null;
      preflight?: LetterPreflight | null;
    }>(
      `/api/dossier/${jobId}/export?lang=${lang}&include_attachments=${includeAttachments}&open_explorer=${openExplorer}`,
      {
        method: 'POST',
      }
    ),

  openDossierFolder: (jobId: number, lang: 'auto' | 'de' | 'en' = 'auto') =>
    requestJson<{ folder: string; files: string[]; file_count: number }>(
      `/api/dossier/${jobId}/open-folder?lang=${lang}`,
      {
        method: 'POST',
      }
    ),

  getSkillGaps: (minScore = 55) =>
    requestJson<SkillGapHeatmapResponse>(`/api/analytics/skill-gaps?min_score=${minScore}`),

  getInterviewPrep: (jobId: number) =>
    requestJson<InterviewPrepRecord>(`/api/interview-prep/${jobId}`),

  generateInterviewPrep: (jobId: number, lang = 'de', model = 'hydrogen') =>
    requestJson<InterviewPrepRecord>(`/api/interview-prep/${jobId}`, {
      method: 'POST',
      body: JSON.stringify({ lang, model }),
    }),

  getPipelineSankey: () =>
    requestJson<PipelineSankeyResponse>('/api/pipeline/sankey'),

  getCompanies: () =>
    requestJson<CompaniesOverviewResponse>('/api/companies'),

  reorderPrioritizedJobs: (jobIds: number[]) =>
    requestJson<{ ordered_ids: number[] }>('/api/jobs/prioritized/reorder', {
      method: 'POST',
      body: JSON.stringify({ job_ids: jobIds }),
    }),

  getPrioritizedRanking: () =>
    requestJson<{
      ranking: PrioritizedAiRanking | null;
      company_history: Record<string, CompanyHistoryEntry[]>;
    }>('/api/jobs/prioritized/ranking'),

  aiRankPrioritizedJobs: (
    focus: PriorityFocus,
    instruction: string,
    model = 'hydrogen',
    applyOrder = true
  ) =>
    requestJson<{
      ranking: PrioritizedAiRanking;
      company_history: Record<string, CompanyHistoryEntry[]>;
      applied: boolean;
    }>('/api/jobs/prioritized/ai-rank', {
      method: 'POST',
      body: JSON.stringify({ focus, instruction, model, apply_order: applyOrder }),
    }),

  listLetterParagraphs: (lang = '') =>
    requestJson<LetterParagraphItem[]>(
      `/api/letters/paragraphs${lang ? `?lang=${encodeURIComponent(lang)}` : ''}`
    ),

  saveLetterParagraph: (payload: {
    text: string;
    lang?: string;
    category?: string;
    label?: string;
    company?: string;
    job_title?: string;
    job_id?: number | null;
    source_kind?: string;
  }) =>
    requestJson<{ item: LetterParagraphItem; items: LetterParagraphItem[] }>(
      '/api/letters/paragraphs',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    ),

  deleteLetterParagraph: (paragraphId: number) =>
    requestJson<{ deleted: boolean }>(`/api/letters/paragraphs/${paragraphId}`, {
      method: 'DELETE',
    }),

  recommendLetterParagraphs: (jobId: number, lang = '', model = 'hydrogen') =>
    requestJson<ParagraphRecommendResponse>('/api/letters/paragraphs/recommend', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, lang, model }),
    }),

  createLetterFromTemplate: (sourceLetterId: number, jobId: number) =>
    requestJson<CoverLetter>('/api/letters/from-template', {
      method: 'POST',
      body: JSON.stringify({ source_letter_id: sourceLetterId, job_id: jobId }),
    }),

  adaptLetterBlocks: (payload: {
    job_id: number | null;
    paragraphs: string[];
    lang: string;
    model: string;
  }) =>
    requestJson<AdaptBlocksResponse>('/api/letters/adapt-blocks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
