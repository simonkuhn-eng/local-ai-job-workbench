export interface ScanRunSummary {
  id: number;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  sources_total: number;
  sources_ok: number;
  sources_failed: number;
  found_count: number;
  new_count: number;
  closed_count: number;
  relisted_count: number;
  changed_count: number;
  new_job_ids?: number[];
  closed_job_ids?: number[];
  relisted_job_ids?: number[];
  errors: string[];
  legacy?: boolean;
}

export interface LifecycleSummary {
  total_stored: number;
  active_all: number;
  active_relevant: number;
  relevant_all: number;
  closed_relevant: number;
  rule_eligible_pending: number;
  new_7d: number;
  closed_all: number;
  closed_7d: number;
  closed_30d: number;
  relisted_30d: number;
  bookmarked: number;
  prioritized?: number;
  prioritized_active?: number;
  uninteresting?: number;
  ai_evaluated: number;
  pipeline_active: number;
  pipeline_counts: Record<string, number>;
  tier_counts: Array<{ tier: number; label: string; active: number; closed: number }>;
  median_online_days: number | null;
  latest_scan: ScanRunSummary | null;
  feed_warnings: Array<{ source: string; company: string; warning: string; last_check: string }>;
}

export interface ScanLiveStatus {
  running: boolean;
  started_at: string;
  finished_at: string;
  sources_total: number;
  sources_done: number;
  sources_ok: number;
  sources_failed: number;
  current_source: string;
  found_count: number;
  new_count: number;
  closed_count: number;
  relisted_count: number;
  changed_count: number;
  errors: string[];
  scan_run_id: number | null;
}

export interface AiStatus {
  installed: boolean;
  logged_in: boolean;
  executable: string;
  models: Record<string, string>;
  message: string;
}

export interface AiBatchResultItem {
  id: number;
  company: string;
  title: string;
  location: string;
  ai_score: number;
  rule_score: number;
  eligible: boolean;
  summary: string;
}

export interface AiBatchStatus {
  running: boolean;
  cancelling?: boolean;
  total: number;
  done: number;
  current_job: string;
  model: string;
  errors: string[];
  results?: AiBatchResultItem[];
}

export interface OverviewResponse {
  lifecycle: LifecycleSummary;
  scan: ScanLiveStatus;
  ai_status: AiStatus;
  ai_batch: AiBatchStatus;
  personal: {
    name: string;
    street: string;
    zip: string;
    city: string;
    phone: string;
    email: string;
  };
}

export interface JobSummary {
  id: number;
  company: string;
  title: string;
  location: string;
  url: string;
  source: string;
  tier: number;
  tier_label: string;
  preference: 'preferred' | 'neutral' | 'excluded';
  active: boolean;
  status: string;
  status_label: string;
  bookmarked: boolean;
  priority: number;
  priority_rank?: number;
  feedback: string;
  feedback_label?: string;
  note: string;
  next_step: string;
  contact: string;
  application_date: string;
  deadline: string;
  followup_date: string;
  first_seen: string;
  first_seen_date: string;
  first_seen_short: string;
  first_seen_full: string;
  last_seen: string;
  changed_at: string;
  unavailable_since: string;
  disappeared_date: string;
  unavailable_reason: string;
  unavailable_reason_label: string;
  age_days: number;
  online_duration_days: number | null;
  offline_days: number | null;
  is_new_scan: boolean;
  is_new_7d: boolean;
  was_relisted: boolean;
  tenure_kind: 'fresh' | 'active' | 'relisted' | 'closed';
  tenure_badge: string;
  rule_score: number;
  rule_reason: string;
  hard_exclusion: string;
  rule_eligible: boolean;
  rule_ineligible_reason: string;
  ai_score: number | null;
  ai_model: string;
  ai_date: string;
  rank_score: number;
  score_source: 'agy' | 'ai_legacy' | 'rules';
  summary_reason: string;
  fit_band: string;
  role_tag: string;
  work_mode: string;
  has_cv: boolean;
  has_letter: boolean;
  export_folder: string;
  folder_exists: boolean;
  is_unviewed: boolean;
  ai_is_stale?: boolean;
}

export interface DeepRequirement {
  requirement: string;
  importance: 'must' | 'nice' | 'unclear';
  ad_evidence: string;
  profile_evidence: string;
  gap: string;
  verdict: 'matched' | 'partial' | 'missing' | 'unknown';
}

export interface DeepReview {
  summary: string;
  requirements: DeepRequirement[];
  project_hooks: string[];
  interview_questions: string[];
}

export interface StructuredDescriptionSection {
  category: 'overview' | 'tasks' | 'profile' | 'benefits';
  title: string;
  kind: 'prose' | 'list';
  paragraphs: string[];
  items: Array<{ lead: string; text: string }>;
}

export interface JobDetail extends JobSummary {
  description: string;
  structured_description: StructuredDescriptionSection[];
  interview_notes: string;
  duplicate_applied_warning?: string;
  ai_parsed: {
    skills: number;
    tasks: number;
    seniority: number;
    domain: number;
    eligible: boolean;
    employment: string;
    confidence: string;
    reason: string;
    evidence: string[];
    gaps: string[];
    work_authorization: string;
    classification?: {
      role: string;
      sectors: string[];
      entry_level: string;
      fit_band: string;
      required_years: number | null;
      experience_importance: string;
      experience_evidence: string;
      work_mode: string;
      remote_restrictions: string;
      flags: string[];
    };
  } | null;
  ai_deep_review: DeepReview | null;
  availability_history: Array<{
    id: number;
    event: 'unlisted' | 'relisted';
    observed_at: string;
    source: string;
    reason: string;
  }>;
  workflow_events: Array<{
    id: number;
    kind: string;
    old_value: Record<string, unknown>;
    new_value: Record<string, unknown>;
    created: string;
  }>;
  cv_variants: Array<{
    id: number;
    name: string;
    lang: string;
    pdf_de_ready: boolean;
    pdf_en_ready: boolean;
    updated_at: string;
  }>;
  cover_letters: Array<{
    id: number;
    name: string;
    lang: string;
    subject: string;
    pdf_ready: boolean;
    updated_at: string;
  }>;
}

export interface CvSkill {
  name_de: string;
  name_en: string;
  level_de: string;
  level_en: string;
}

export interface CvExperience {
  timeframe: string;
  title_de: string;
  title_en: string;
  company: string;
  bullets_de: string[];
  bullets_en: string[];
}

export interface CvEduBullet {
  label: string;
  text: string;
}

export interface CvEducation {
  timeframe: string;
  degree_de: string;
  degree_en: string;
  institution: string;
  bullets_de: CvEduBullet[];
  bullets_en: CvEduBullet[];
}

export interface CvLayoutSettings {
  accent_color?: string;
  main_color?: string;
  dark_color?: string;
  light_color?: string;
  column_ratio?: number;
  font_size?: string;
  section_spacing?: string;
  margins?: {
    top?: string;
    bottom?: string;
    left?: string;
    right?: string;
  };
  section_order_left?: string[];
  section_order_right?: string[];
  section_titles?: Record<string, { de?: string; en?: string } | string>;
  show_profile?: boolean;
  show_interests?: boolean;
  show_languages?: boolean;
  show_skills?: boolean;
  show_contact?: boolean;
  show_signature?: boolean;
  density?: 'compact' | 'normal' | 'relaxed' | 'generous';
  left_section_spacing?: string;
  skill_item_spacing?: string;
  skill_lang_spacing?: string;
  interest_item_spacing?: string;
  contact_item_spacing?: string;
  main_section_spacing?: string;
  event_spacing?: string;
  item_spacing?: string;
  portrait_align?: 'left' | 'center' | 'right';
  portrait_width?: number;
  portrait_offset_x?: number;
  portrait_offset_y?: number;
}

export interface CvData extends Partial<CvLayoutSettings> {
  header_title: { de: string; en: string };
  role_line1: { de: string; en: string };
  role_line2: { de: string; en: string };
  skills: CvSkill[];
  languages: CvSkill[];
  interests: Array<{ de: string; en: string }>;
  profile: { de: string; en: string };
  experience: CvExperience[];
  education: CvEducation[];
  signature_city: string;
  include_signature: boolean;
  include_portrait: boolean;
  layout?: CvLayoutSettings;
}

export interface PdfVerificationCheck {
  id: string;
  label: string;
  ok: boolean;
}

export interface PdfVerificationResult {
  status: 'ok' | 'warn' | 'error';
  page_count: number;
  char_count: number;
  checks: PdfVerificationCheck[];
  issues: Array<{ severity: string; message: string }>;
  keyword_coverage?: {
    coverage_pct: number | null;
    matched: string[];
    missing: string[];
  } | null;
}

export interface CvVariant {
  id: number;
  job_id: number | null;
  name: string;
  lang: 'de' | 'en';
  pdf_de_ready: boolean;
  pdf_en_ready: boolean;
  pages_de: number;
  pages_en: number;
  created_at: string;
  updated_at: string;
  cv_data?: CvData;
  history?: Array<{ ts: string; label: string }>;
  warnings?: string[];
  verification?: PdfVerificationResult | null;
  verification_de?: PdfVerificationResult | null;
  verification_en?: PdfVerificationResult | null;
}

export interface AddressSourceInfo {
  kind: 'job_posting' | 'company_registry' | 'job_location' | 'fallback';
  label: string;
  url: string;
  snippet?: string;
  verification_url?: string;
  confidence?: 'high' | 'medium' | 'fallback';
}

export interface AutofillAddressResult {
  company: string;
  contact_person: string;
  street: string;
  zip_city: string;
  job_ref: string;
  subject: string;
  source: AddressSourceInfo;
}

export interface LetterPreflight {
  ok: boolean;
  blockers: string[];
  warnings: string[];
}

export interface AdaptBlocksResponse {
  adapted_paragraphs: string[];
  body_text: string;
  changes: string[];
  summary: string;
  model: string;
}

export interface CoverLetter {
  id: number | null;
  job_id: number | null;
  name: string;
  lang: 'de' | 'en';
  company: string;
  contact_person: string;
  street: string;
  zip_city: string;
  job_ref: string;
  place: string;
  date_str: string;
  subject: string;
  salutation: string;
  body_text: string;
  closing: string;
  include_signature: boolean;
  signature_width_cm: number;
  pdf_ready: boolean;
  pages: number;
  warning?: string;
  verification?: PdfVerificationResult | null;
  preflight?: LetterPreflight | null;
  pdf_stale?: boolean;
  template_replacements?: string[];
  address_source?: AddressSourceInfo | null;
  updated_at?: string;
}

export interface StarStory {
  question: string;
  project: string;
  situation_task: string;
  action: string;
  result: string;
}

export interface GapBridge {
  gap: string;
  honest_bridge: string;
}

export interface InterviewPrepData {
  job_id: number;
  company: string;
  title: string;
  model?: string;
  pitch_60s: string;
  star_stories: StarStory[];
  gap_bridges: GapBridge[];
  smart_questions: string[];
}

export interface InterviewPrepRecord {
  job_id: number;
  model: string;
  updated_at: string;
  prep: InterviewPrepData | null;
}

export interface SkillGapItem {
  topic: string;
  count: number;
  examples: string[];
  companies: string[];
}

export interface SankeyNode {
  id: string;
  label: string;
  stage: number;
  color: string;
  value: number;
}

export interface SankeyFlow {
  source: string;
  target: string;
  source_label: string;
  target_label: string;
  value: number;
}

export interface PipelineSankeyResponse {
  pipeline_total: number;
  interessant: number;
  vorbereiten: number;
  applied_total: number;
  warten: number;
  direkt_absage: number;
  interview_total: number;
  interview_aktiv: number;
  interview_absage: number;
  angebot: number;
  angenommen: number;
  response_rate: number;
  interview_rate: number;
  offer_rate: number;
  nodes: SankeyNode[];
  flows: SankeyFlow[];
  sankeymatic_text: string;
  demo_nodes: SankeyNode[];
  demo_flows: SankeyFlow[];
  demo_text: string;
  is_empty: boolean;
}

export interface SkillGapHeatmapResponse {
  analyzed_jobs: number;
  items: SkillGapItem[];
}

export interface CompanyFeedInfo {
  source: string;
  type: string;
  parser: string;
  url: string;
  enabled: boolean;
  complete_feed: boolean;
  last_check: string;
  current_count: number | null;
  last_good_count: number | null;
  warning: string;
  status: 'ok' | 'error' | 'warning' | 'empty' | 'pending' | 'disabled';
}

export interface CompanySampleJob {
  id: number;
  title: string;
  location: string;
  score: number;
  tier: number;
  active: boolean;
  status: string;
  url: string;
}

export interface CompanyOverviewItem {
  company: string;
  integration: 'feed' | 'reference' | 'manual';
  status: 'ok' | 'error' | 'warning' | 'empty' | 'pending' | 'excluded' | 'reference' | 'manual';
  status_label: string;
  preference: 'preferred' | 'neutral' | 'excluded';
  last_check: string;
  error_message: string;
  primary_url: string;
  official_careers_url: string;
  focus: string;
  notes: string;
  search_priority: string;
  feeds: CompanyFeedInfo[];
  active_jobs: number;
  total_jobs: number;
  closed_jobs: number;
  bayern_active: number;
  bayern_total: number;
  qualified_active: number;
  max_score: number;
  avg_score: number;
  applied_count: number;
  bookmarked_count: number;
  first_seen_min: string;
  last_seen_max: string;
  sample_jobs: CompanySampleJob[];
}

export interface CompaniesOverviewSummary {
  connected_total: number;
  ok_count: number;
  error_count: number;
  warning_count: number;
  empty_count: number;
  pending_count: number;
  excluded_count: number;
  reference_count: number;
  manual_count: number;
  total_companies: number;
  total_active_jobs: number;
  total_historical_jobs: number;
  latest_scan_at: string;
}

export interface CompaniesOverviewResponse {
  summary: CompaniesOverviewSummary;
  items: CompanyOverviewItem[];
}

export interface LetterParagraphItem {
  id: number;
  text: string;
  text_hash: string;
  lang: 'de' | 'en';
  category: 'intro' | 'experience' | 'projects' | 'closing' | 'body';
  label: string;
  company: string;
  job_title: string;
  job_id: number | null;
  source_kind: 'sent' | 'saved' | 'manual';
  usage_count: number;
  created_at: string;
  updated_at: string;
}

export interface ParagraphRecommendation {
  paragraph_id: number;
  fit_score: number;
  recommended: boolean;
  suggested_slot: number;
  reason: string;
}

export interface ParagraphRecommendResponse {
  summary: string;
  model: string;
  recommendations: ParagraphRecommendation[];
}

export type PriorityFocus = 'balanced' | 'chance' | 'location' | 'urgency';
export type PriorityWave = 'jetzt' | 'danach' | 'spaeter' | 'pruefen';

export interface CompanyHistoryEntry {
  job_id: number;
  company: string;
  title: string;
  status: string;
  status_label: string;
  application_date: string;
}

export interface PrioritizedRankingItem {
  job_id: number;
  rank: number;
  wave: PriorityWave;
  chance_level: 'hoch' | 'mittel' | 'ambitioniert';
  urgency: 'hoch' | 'mittel' | 'niedrig';
  scores: {
    chance: number;
    fit: number;
    location: number;
    timing: number;
  };
  rationale: string;
  tradeoff: string;
  next_action: string;
}

export interface PrioritizedAiRanking {
  created_at: string;
  model: string;
  focus: PriorityFocus | string;
  instruction: string;
  strategy_summary: string;
  evaluated_count: number;
  previous_order: number[];
  ranked_ids: number[];
  items: PrioritizedRankingItem[];
  items_by_id: Record<string, PrioritizedRankingItem>;
}
