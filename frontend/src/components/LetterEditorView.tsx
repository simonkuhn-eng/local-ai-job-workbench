import React, { useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Briefcase,
  Building2,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CopyPlus,
  Cpu,
  Download,
  ExternalLink,
  FilePlus2,
  FileText,
  FolderOpen,
  Layers,
  Mail,
  MapPin,
  Maximize2,
  Minimize2,
  PenLine,
  RefreshCw,
  Sparkles,
  Trash2,
  Wand2,
  Zap,
} from 'lucide-react';
import { api } from '../api';
import { LetterParagraphOrganizer } from './LetterParagraphOrganizer';
import type {
  CoverLetter,
  JobDetail,
  JobSummary,
  LetterParagraphItem,
  ParagraphRecommendation,
} from '../types';

interface LetterEditorViewProps {
  isActive: boolean;
  activeLetterId: number | null;
  selectedJobId: number | null;
  onSelectLetter: (letterId: number | null) => void;
  onSelectJob?: (jobId: number | null) => void;
  onOpenCvStudio?: (jobId: number) => void;
  onBusyChange?: (busy: boolean) => void;
  onNotify: (msg: string) => void;
  onBackToRadar?: (jobId: number) => void;
}

const DEFAULT_SALUTATION = {
  de: 'Sehr geehrte Damen und Herren,',
  en: 'Dear Hiring Team,',
};

const DEFAULT_CLOSING = {
  de: 'Mit freundlichen Grüßen',
  en: 'Kind regards,',
};

const LETTER_AI_PRESETS = [
  {
    label: '🎯 Problem-Löser & Zukunftsfokus',
    text: 'Fokus auf Zukunft und Problemlösung für das Unternehmen: keine chronologische CV-Wiederholung, sondern zeige konkret, wie meine Skills deren Vorhaben voranbringen.',
  },
  {
    label: '✨ Vollständiger Entwurf',
    text: 'Verfasse einen kompletten, überzeugenden Entwurf passend zu dieser Stellenausschreibung.',
  },
  {
    label: '🤖 AI Agents, LLM & Backend',
    text: 'Betone angewandte LLM-Systeme, Agentic Workflows, FastAPI, RAG und Docker aus meinem Profil.',
  },
  {
    label: '🦾 Robotik, ROS 2 & Sensorik',
    text: 'Richte den Fokus stärker auf Robotik, ROS 2, Sensorfusion, Computer Vision und Embedded-Integration.',
  },
  {
    label: '⚡ Auf 1 Seite straffen',
    text: 'Überarbeite meinen aktuellen Brieftext: sprachlich prägnanter, souveräner und exakt auf 1 DIN-A4-Seite.',
  },
];

export const LetterEditorView: React.FC<LetterEditorViewProps> = ({
  isActive,
  activeLetterId,
  selectedJobId,
  onSelectLetter,
  onSelectJob,
  onOpenCvStudio,
  onBusyChange,
  onNotify,
  onBackToRadar,
}) => {
  const [letters, setLetters] = useState<CoverLetter[]>([]);
  const [draft, setDraft] = useState<CoverLetter | null>(null);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const isDirtyRef = React.useRef(false);
  const editVersionRef = React.useRef(0);
  const loadReqIdRef = React.useRef(0);
  const autoSaveTimerRef = React.useRef<number | null>(null);
  const draftRef = React.useRef<CoverLetter | null>(null);
  draftRef.current = draft;
  const lastLoadedLetterIdRef = React.useRef<number | null>(null);
  const skipNextInitReloadRef = React.useRef(false);

  const [pipelineJobs, setPipelineJobs] = useState<JobSummary[]>([]);
  const [linkedJob, setLinkedJob] = useState<JobDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiModel, setAiModel] = useState<'hydrogen' | 'argon' | 'neon'>('argon');
  const [aiInstruction, setAiInstruction] = useState('');
  const [aiSummary, setAiSummary] = useState('');
  const [removedPatterns, setRemovedPatterns] = useState<string[]>([]);
  const [auditWarnings, setAuditWarnings] = useState<string[]>([]);
  const [autofillingAddress, setAutofillingAddress] = useState(false);
  const [showJobCheatSheet, setShowJobCheatSheet] = useState(false);
  const [pdfNonce, setPdfNonce] = useState(() => Date.now());
  const [viewMode, setViewMode] = useState<'width' | 'page'>('width');
  const [layoutPreset, setLayoutPreset] = useState<'balanced' | 'preview' | 'editor'>('balanced');
  const [bodyTextSize, setBodyTextSize] = useState<'normal' | 'tall' | 'auto'>('tall');
  const [bodyEditorMode, setBodyEditorMode] = useState<'organizer' | 'text' | 'both'>('organizer');
  const [isBodyTextModalOpen, setIsBodyTextModalOpen] = useState(false);

  const [paragraphs, setParagraphs] = useState<LetterParagraphItem[]>([]);
  const [loadingParagraphs, setLoadingParagraphs] = useState(false);
  const [paragraphRecs, setParagraphRecs] = useState<Record<number, ParagraphRecommendation>>({});
  const [recSummary, setRecSummary] = useState('');
  const [recBusy, setRecBusy] = useState(false);
  const [adaptBusy, setAdaptBusy] = useState(false);

  useEffect(() => {
    onBusyChange?.(busy || aiBusy || recBusy || adaptBusy);
  }, [busy, aiBusy, recBusy, adaptBusy, onBusyChange]);

  const loadParagraphs = async () => {
    setLoadingParagraphs(true);
    try {
      const items = await api.listLetterParagraphs();
      setParagraphs(items);
    } catch {
      // ignore initial paragraph fetch failure
    } finally {
      setLoadingParagraphs(false);
    }
  };

  useEffect(() => {
    void loadParagraphs();
  }, []);

  const refreshLettersList = async () => {
    const list = await api.listLetters();
    setLetters(list);
    return list;
  };

  useEffect(() => {
    api
      .listJobs({ lifecycle: 'relevant_all', limit: 250 })
      .then((res) => {
        setPipelineJobs(res.items.filter((j) => j.status !== 'uninteressant'));
      })
      .catch(() => {});
  }, []);

  const flushDirtyLetterDraft = async () => {
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    if (!isDirtyRef.current || !draftRef.current) return;
    const cur = draftRef.current;
    if (!cur.job_id && !cur.id && !cur.body_text.trim() && !cur.company.trim()) return;
    const ver = editVersionRef.current;
    try {
      setIsSavingDraft(true);
      const saved = await api.saveLetterDraft(cur);
      if (editVersionRef.current === ver) {
        isDirtyRef.current = false;
        setDraftSavedAt(
          new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        );
        setDraft((prev) =>
          prev
            ? {
                ...prev,
                id: saved.id || prev.id,
                preflight: saved.preflight,
                pdf_stale: saved.pdf_stale,
              }
            : saved
        );
      }
      if (saved.id && saved.id !== cur.id) {
        lastLoadedLetterIdRef.current = saved.id;
        skipNextInitReloadRef.current = true;
        onSelectLetter(saved.id);
        void refreshLettersList();
      }
    } catch (err) {
      console.error('Flush letter draft failed', err);
    } finally {
      setIsSavingDraft(false);
    }
  };

  useEffect(() => {
    if (!isActive) {
      void flushDirtyLetterDraft();
    }
  }, [isActive]);

  useEffect(() => {
    if (skipNextInitReloadRef.current) {
      skipNextInitReloadRef.current = false;
      return;
    }
    let cancelled = false;
    const reqId = ++loadReqIdRef.current;
    const init = async () => {
      await flushDirtyLetterDraft();
      if (cancelled || reqId !== loadReqIdRef.current) return;
      setBusy(true);
      try {
        const list = await refreshLettersList();
        if (cancelled || reqId !== loadReqIdRef.current) return;

        // 1. Explicit activeLetterId requested
        if (activeLetterId) {
          if (draftRef.current?.id === activeLetterId && lastLoadedLetterIdRef.current === activeLetterId) {
            return;
          }
          const loaded = await api.getLetter(activeLetterId);
          if (!cancelled && reqId === loadReqIdRef.current) {
            isDirtyRef.current = false;
            setDraft(loaded);
            lastLoadedLetterIdRef.current = loaded.id ?? null;
            setPdfNonce(Date.now());
            if (loaded.job_id !== selectedJobId) {
              skipNextInitReloadRef.current = true;
              onSelectJob?.(loaded.job_id ?? null);
            }
          }
          return;
        }

        // 2. No activeLetterId, but selectedJobId is present
        if (selectedJobId) {
          const matching = list.find((l) => l.job_id === selectedJobId);
          if (matching && matching.id) {
            const loaded = await api.getLetter(matching.id);
            if (!cancelled && reqId === loadReqIdRef.current) {
              isDirtyRef.current = false;
              setDraft(loaded);
              lastLoadedLetterIdRef.current = loaded.id ?? null;
              skipNextInitReloadRef.current = true;
              onSelectLetter(loaded.id);
              setPdfNonce(Date.now());
            }
            return;
          }

          // No existing letter for this job -> prefill a new draft
          const prefilled = await api.prefillLetter(selectedJobId, 'de');
          if (!cancelled && reqId === loadReqIdRef.current) {
            isDirtyRef.current = false;
            setDraft(prefilled);
            lastLoadedLetterIdRef.current = prefilled.id ?? null;
            if (prefilled.id) {
              skipNextInitReloadRef.current = true;
              onSelectLetter(prefilled.id);
            }
            setPdfNonce(Date.now());
          }
          return;
        }

        // 3. Neither activeLetterId nor selectedJobId: load first existing letter or create blank
        if (list.length > 0 && list[0].id) {
          const first = await api.getLetter(list[0].id);
          if (!cancelled && reqId === loadReqIdRef.current) {
            isDirtyRef.current = false;
            setDraft(first);
            lastLoadedLetterIdRef.current = first.id ?? null;
            skipNextInitReloadRef.current = true;
            onSelectLetter(first.id);
            onSelectJob?.(first.job_id ?? null);
            setPdfNonce(Date.now());
          }
        } else {
          const blank = await api.prefillLetter(null, 'de');
          if (!cancelled && reqId === loadReqIdRef.current) {
            isDirtyRef.current = false;
            setDraft(blank);
            lastLoadedLetterIdRef.current = null;
            setPdfNonce(Date.now());
          }
        }
      } catch (err) {
        if (!cancelled && reqId === loadReqIdRef.current) {
          onNotify(err instanceof Error ? err.message : 'Anschreiben konnte nicht geladen werden');
        }
      } finally {
        if (!cancelled && reqId === loadReqIdRef.current) setBusy(false);
      }
    };
    void init();
    return () => {
      cancelled = true;
    };
  }, [activeLetterId, selectedJobId]);

  // Debounced auto-save of draft into SQLite without requiring LaTeX compile
  useEffect(() => {
    if (!draft || !isDirtyRef.current) return;
    const target = draft;
    if (!target.job_id && !target.id && !target.body_text.trim() && !target.company.trim()) {
      return;
    }
    const ver = editVersionRef.current;
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
    }
    autoSaveTimerRef.current = window.setTimeout(async () => {
      try {
        setIsSavingDraft(true);
        const saved = await api.saveLetterDraft(target);
        if (editVersionRef.current === ver) {
          isDirtyRef.current = false;
          setDraftSavedAt(
            new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          );
          setDraft((prev) =>
            prev
              ? {
                  ...prev,
                  id: saved.id || prev.id,
                  preflight: saved.preflight,
                  pdf_stale: saved.pdf_stale,
                }
              : saved
          );
        }
        if (saved.id && saved.id !== target.id) {
          lastLoadedLetterIdRef.current = saved.id;
          skipNextInitReloadRef.current = true;
          onSelectLetter(saved.id);
          void refreshLettersList();
        }
      } catch (err) {
        console.error('Auto-save letter draft failed', err);
      } finally {
        setIsSavingDraft(false);
      }
    }, 1200);

    return () => {
      if (autoSaveTimerRef.current) {
        window.clearTimeout(autoSaveTimerRef.current);
      }
    };
  }, [draft, onSelectLetter]);

  // Flush dirty draft on unmount
  useEffect(() => {
    return () => {
      if (isDirtyRef.current && draftRef.current) {
        const cur = draftRef.current;
        if (cur.job_id || cur.id) {
          void api.saveLetterDraft(cur);
        }
      }
    };
  }, []);

  useEffect(() => {
    // When a draft is loaded, its job_id determines the linked job.
    // If draft has job_id === null (e.g. general template), no job is linked!
    // If no draft is loaded yet, fall back to selectedJobId.
    const jid = draft ? draft.job_id : selectedJobId;
    if (!jid) {
      setLinkedJob(null);
      return;
    }
    api
      .getJobDetail(jid)
      .then(setLinkedJob)
      .catch(() => setLinkedJob(null));
  }, [draft?.job_id, draft?.id, selectedJobId]);

  // Clear stale Hydrogen paragraph recommendations when target job changes
  useEffect(() => {
    setParagraphRecs({});
    setRecSummary('');
  }, [linkedJob?.id]);

  const handleRunHydrogenParagraphMatch = async () => {
    const targetJobId = linkedJob?.id || draft?.job_id || selectedJobId;
    if (!targetJobId) {
      onNotify('Bitte wähle zuerst eine Ziel-Stelle für den Hydrogen-Abgleich.');
      return;
    }
    setRecBusy(true);
    try {
      const res = await api.recommendLetterParagraphs(targetJobId, draft?.lang || 'de', 'hydrogen');
      const map: Record<number, ParagraphRecommendation> = {};
      for (const r of res.recommendations || []) {
        map[r.paragraph_id] = r;
      }
      setParagraphRecs(map);
      setRecSummary(res.summary || '');
      const topCnt = (res.recommendations || []).filter((r) => r.recommended).length;
      onNotify(
        `Gemini 4 Hydrogen hat ${topCnt} besonders passende Absätze für „${linkedJob?.company || 'Stelle'}“ markiert.`
      );
    } catch (err) {
      onNotify(
        err instanceof Error
          ? err.message
          : 'Hydrogen-Absatzanalyse konnte nicht durchgeführt werden'
      );
    } finally {
      setRecBusy(false);
    }
  };

  const handleAdaptBlocksWithHydrogen = async () => {
    const cur = draftRef.current;
    if (!cur) return;
    const targetJobId = cur.job_id ?? selectedJobId ?? null;
    const paras = (cur.body_text || '')
      .replace(/\r\n/g, '\n')
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (paras.length === 0) {
      onNotify('Bitte stelle zuerst mindestens einen Absatz im Baukasten zusammen.');
      return;
    }
    setAdaptBusy(true);
    try {
      const res = await api.adaptLetterBlocks({
        job_id: targetJobId,
        paragraphs: paras,
        lang: cur.lang || 'de',
        model: 'hydrogen',
      });
      editVersionRef.current += 1;
      isDirtyRef.current = true;
      const nextDraft: CoverLetter = {
        ...cur,
        job_id: targetJobId,
        body_text: res.body_text,
        pdf_stale: true,
      };
      setDraft(nextDraft);
      setAiSummary(
        res.changes.length > 0
          ? `${res.summary} (${res.changes.join(' · ')})`
          : res.summary
      );
      try {
        const ver = editVersionRef.current;
        const saved = await api.saveLetterDraft(nextDraft);
        if (editVersionRef.current === ver) {
          isDirtyRef.current = false;
          setDraftSavedAt(
            new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          );
          setDraft((prev) =>
            prev
              ? {
                  ...prev,
                  id: saved.id || prev.id,
                  preflight: saved.preflight,
                  pdf_stale: saved.pdf_stale,
                }
              : saved
          );
        }
        if (saved.id && saved.id !== cur.id) {
          lastLoadedLetterIdRef.current = saved.id;
          skipNextInitReloadRef.current = true;
          onSelectLetter(saved.id);
          void refreshLettersList();
        }
      } catch {
        // leave dirty for manual save
      }
      onNotify(
        res.changes.length > 0
          ? `Hydrogen-Leichtanpassung abgeschlossen: ${res.changes.join(', ')}`
          : res.summary || 'Bausteine behutsam an die Ziel-Stelle angepasst.'
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Baustein-Anpassung fehlgeschlagen');
    } finally {
      setAdaptBusy(false);
    }
  };

  const handleCloneFromTemplate = async (sourceLetterId: number) => {
    const targetJobId = draft?.job_id ?? selectedJobId;
    if (!targetJobId || !sourceLetterId || busy) return;
    await flushDirtyLetterDraft();
    const reqId = ++loadReqIdRef.current;
    setBusy(true);
    try {
      const cloned = await api.createLetterFromTemplate(sourceLetterId, targetJobId);
      if (reqId !== loadReqIdRef.current) return;
      isDirtyRef.current = false;
      lastLoadedLetterIdRef.current = cloned.id ?? null;
      skipNextInitReloadRef.current = cloned.id !== draft?.id;
      setDraft(cloned);
      if (cloned.id) onSelectLetter(cloned.id);
      await refreshLettersList();
      setPdfNonce(Date.now());
      const reps = cloned.template_replacements || [];
      onNotify(
        reps.length > 0
          ? `Vorlage geklont & angepasst (${reps.join(', ')}). Jetzt prüfen & PDF kompilieren.`
          : `Vorlage für ${cloned.company || 'diese Stelle'} übernommen.`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Vorlage konnte nicht übernommen werden');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveParagraphToLibrary = async (text: string, idx: number, total: number) => {
    try {
      const res = await api.saveLetterParagraph({
        text,
        lang: draft?.lang || 'de',
        category: idx === 0 && total >= 2 ? 'intro' : idx === total - 1 && total >= 2 ? 'closing' : '',
        company: draft?.company || linkedJob?.company || '',
        job_title: linkedJob?.title || draft?.subject || '',
        job_id: draft?.job_id ?? linkedJob?.id ?? null,
        source_kind: 'manual',
      });
      setParagraphs(res.items);
      onNotify('Absatz dauerhaft in deine Auswahl übernommen.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Absatz konnte nicht gespeichert werden');
    }
  };

  const handleDeleteParagraphFromLibrary = async (paragraphId: number) => {
    try {
      await api.deleteLetterParagraph(paragraphId);
      setParagraphs((prev) => prev.filter((p) => p.id !== paragraphId));
      onNotify('Absatz aus der Auswahl entfernt.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Absatz konnte nicht gelöscht werden');
    }
  };

  const handleSwitchLanguage = (nextLang: 'de' | 'en') => {
    if (!draft || draft.lang === nextLang) return;
    const prevLang = draft.lang;
    const updatedSalutation =
      !draft.salutation.trim() || draft.salutation.trim() === DEFAULT_SALUTATION[prevLang]
        ? DEFAULT_SALUTATION[nextLang]
        : draft.salutation;
    const isDefaultClosing =
      !draft.closing.trim() ||
      draft.closing.trim() === DEFAULT_CLOSING[prevLang] ||
      (prevLang === 'en' && draft.closing.trim() === 'Sincerely,');
    const updatedClosing = isDefaultClosing ? DEFAULT_CLOSING[nextLang] : draft.closing;
    let updatedSubject = draft.subject;
    if (nextLang === 'en' && updatedSubject.startsWith('Bewerbung als ')) {
      updatedSubject = `Application for ${updatedSubject.replace(/^Bewerbung als\s+/, '')}`;
    } else if (nextLang === 'de' && updatedSubject.startsWith('Application for ')) {
      updatedSubject = `Bewerbung als ${updatedSubject.replace(/^Application for\s+/, '')}`;
    }
    editVersionRef.current += 1;
    isDirtyRef.current = true;
    setDraft({
      ...draft,
      lang: nextLang,
      salutation: updatedSalutation,
      closing: updatedClosing,
      subject: updatedSubject,
      pdf_stale: true,
    });
  };

  const handleNewBlank = async () => {
    await flushDirtyLetterDraft();
    const reqId = ++loadReqIdRef.current;
    setBusy(true);
    try {
      const blank = await api.prefillLetter(null, draft?.lang || 'de');
      if (reqId !== loadReqIdRef.current) return;
      isDirtyRef.current = false;
      setDraft(blank);
      lastLoadedLetterIdRef.current = null;
      skipNextInitReloadRef.current = true;
      onSelectLetter(null);
      onSelectJob?.(null);
      setPdfNonce(Date.now());
      onNotify('Neuer, ungespeicherter Entwurf angelegt.');
    } catch (err) {
      if (reqId === loadReqIdRef.current) {
        onNotify(err instanceof Error ? err.message : 'Neuer Entwurf fehlgeschlagen');
      }
    } finally {
      if (reqId === loadReqIdRef.current) setBusy(false);
    }
  };

  const handlePrefillFromSelectedJob = async () => {
    if (!selectedJobId) {
      onNotify('Bitte wähle zuerst im Stellen-Radar eine Stelle aus.');
      return;
    }
    await flushDirtyLetterDraft();
    const reqId = ++loadReqIdRef.current;
    setBusy(true);
    try {
      const prefilled = await api.prefillLetter(selectedJobId, draft?.lang || 'de');
      if (reqId !== loadReqIdRef.current) return;
      isDirtyRef.current = false;
      setDraft(prefilled);
      lastLoadedLetterIdRef.current = prefilled.id ?? null;
      skipNextInitReloadRef.current = true;
      onSelectLetter(prefilled.id);
      if (prefilled.id) setPdfNonce(Date.now());
      onNotify(
        prefilled.id
          ? `Vorhandenes Anschreiben für ${prefilled.company} geladen.`
          : `Empfänger & Betreff für ${prefilled.company} vorausgefüllt.`
      );
    } catch (err) {
      if (reqId === loadReqIdRef.current) {
        onNotify(err instanceof Error ? err.message : 'Vorausfüllen fehlgeschlagen');
      }
    } finally {
      if (reqId === loadReqIdRef.current) setBusy(false);
    }
  };

  const handleAutofillAddress = async () => {
    if (!draft) return;
    const targetJobId = draft.job_id || selectedJobId || null;
    setAutofillingAddress(true);
    try {
      const res = await api.autofillAddress({
        job_id: targetJobId,
        company: draft.company || linkedJob?.company || '',
        location: linkedJob?.location || '',
        title: linkedJob?.title || '',
        lang: draft.lang || 'de',
      });

      editVersionRef.current += 1;
      isDirtyRef.current = true;
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          company: res.company || prev.company,
          contact_person: res.contact_person || prev.contact_person,
          street: res.street || prev.street,
          zip_city: res.zip_city || prev.zip_city,
          job_ref: res.job_ref || prev.job_ref,
          subject: prev.subject.trim() ? prev.subject : (res.subject || prev.subject),
          address_source: res.source,
          pdf_stale: true,
        };
      });

      const parts: string[] = [];
      if (res.company) parts.push(res.company);
      if (res.street) parts.push(res.street);
      if (res.zip_city) parts.push(res.zip_city);
      if (res.job_ref) parts.push(`Ref: ${res.job_ref}`);

      if (parts.length > 0) {
        onNotify(`Anschrift automatisch ermittelt (${res.source.label}): ${parts.join(', ')}`);
      } else {
        onNotify('Keine Anschrift im Ausschreibungstext oder Verzeichnis gefunden.');
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Fehler beim automatischen Ermitteln der Anschrift');
    } finally {
      setAutofillingAddress(false);
    }
  };

  const handleCompile = async (): Promise<CoverLetter | null> => {
    const cur = draftRef.current;
    if (!cur || busy) return null;
    if (!cur.body_text.trim()) {
      onNotify('Bitte füge zuerst den Brieftext ein, bevor das PDF generiert wird.');
      return null;
    }
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    const ver = editVersionRef.current;
    setBusy(true);
    try {
      const compiled = await api.compileLetter({
        ...cur,
        name:
          cur.name && cur.name !== 'Neues Anschreiben'
            ? cur.name
            : `${cur.company || 'Anschreiben'} — ${cur.subject.slice(0, 36) || cur.date_str}`,
      });
      if (editVersionRef.current === ver) {
        isDirtyRef.current = false;
      }
      lastLoadedLetterIdRef.current = compiled.id ?? null;
      skipNextInitReloadRef.current = compiled.id !== cur.id;
      setDraft(compiled);
      onSelectLetter(compiled.id);
      await refreshLettersList();
      void loadParagraphs();
      setPdfNonce(Date.now());
      if (compiled.warning) {
        onNotify(`PDF erstellt (${compiled.warning})`);
      } else {
        onNotify('Anschreiben-PDF erfolgreich auf 1 Seite gesetzt.');
      }
      return compiled;
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'LaTeX-Kompilierung fehlgeschlagen');
      return null;
    } finally {
      setBusy(false);
    }
  };

  const updateDraftField = <K extends keyof CoverLetter>(field: K, value: CoverLetter[K]) => {
    editVersionRef.current += 1;
    isDirtyRef.current = true;
    setDraft((prev) => (prev ? { ...prev, [field]: value, pdf_stale: true } : prev));
  };

  const handleAiGenerate = async (customInstruction?: string, proofreadOnly = false, humanizeOnly = false) => {
    if (!draft || aiBusy) return;
    if (proofreadOnly && !draft.body_text.trim()) {
      onNotify('Bitte füge zuerst den Entwurfstext ein, der auf Grammatik & Rechtschreibung geprüft werden soll.');
      return;
    }
    if (humanizeOnly && !draft.body_text.trim()) {
      onNotify('Bitte füge zuerst den Entwurfstext ein, der humanisiert werden soll.');
      return;
    }
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    setAiBusy(true);
    try {
      const targetJobId = draft.job_id ?? selectedJobId ?? null;
      const gen = await api.generateLetter({
        job_id: targetJobId,
        lang: draft.lang,
        model: aiModel,
        instruction: customInstruction ?? aiInstruction,
        current_draft: draft,
        proofread_only: proofreadOnly,
        humanize_only: humanizeOnly,
      });

      const nextDraft: CoverLetter = {
        ...draft,
        job_id: targetJobId,
        subject: gen.subject || draft.subject,
        salutation: gen.salutation || draft.salutation,
        contact_person: gen.contact_person ?? draft.contact_person,
        body_text: gen.body_text || draft.body_text,
        closing: gen.closing || draft.closing,
      };
      setDraft(nextDraft);
      setAiSummary(gen.summary);
      setRemovedPatterns(gen.removed_patterns || []);
      setAuditWarnings(gen.audit_warnings || []);

      // Persist draft to SQLite immediately so progress is never lost even if LaTeX errors
      try {
        const savedDraft = await api.saveLetterDraft(nextDraft);
        if (savedDraft.id) {
          nextDraft.id = savedDraft.id;
          lastLoadedLetterIdRef.current = savedDraft.id;
          skipNextInitReloadRef.current = true;
          onSelectLetter(savedDraft.id);
        }
        isDirtyRef.current = false;
        setDraftSavedAt(
          new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        );
        await refreshLettersList();
      } catch (saveErr) {
        console.error('Save letter draft failed', saveErr);
      }

      if (nextDraft.body_text.trim()) {
        try {
          const compiled = await api.compileLetter({
            ...nextDraft,
            name:
              nextDraft.name && nextDraft.name !== 'Neues Anschreiben'
                ? nextDraft.name
                : `${nextDraft.company || 'Anschreiben'} — ${nextDraft.subject.slice(0, 36) || nextDraft.date_str}`,
          });
          lastLoadedLetterIdRef.current = compiled.id ?? null;
          skipNextInitReloadRef.current = compiled.id !== nextDraft.id;
          setDraft(compiled);
          onSelectLetter(compiled.id);
          await refreshLettersList();
          setPdfNonce(Date.now());
        } catch (compileErr) {
          onNotify(
            `Entwurf als Draft gesichert, PDF-Warnung: ${
              compileErr instanceof Error ? compileErr.message : 'Prüfung empfohlen'
            }`
          );
        }
      }
      onNotify(
        humanizeOnly
          ? `Humanize abgeschlossen: ${gen.summary || 'KI-Muster bereinigt, Text menschlich formuliert.'}`
          : proofreadOnly
          ? `Korrektur abgeschlossen: ${gen.summary || 'Rechtschreibung & Grammatik geprüft (Inhalt erhalten).'}`
          : `${aiModel === 'neon' ? 'Gemini 4 Neon' : aiModel === 'argon' ? 'Gemini 4 Argon' : 'Gemini 4 Hydrogen'}: ${gen.summary || 'Anschreiben erstellt & als PDF gesetzt.'}`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'KI-Entwurf konnte nicht erstellt werden');
    } finally {
      setAiBusy(false);
    }
  };

  const ensureLetterCompiledAndChecked = async (): Promise<boolean> => {
    const cur = draftRef.current;
    if (!cur) return true;
    let latest: CoverLetter | null = cur;
    if (cur.body_text.trim() && (isDirtyRef.current || cur.pdf_stale || !cur.pdf_ready)) {
      latest = await handleCompile();
      if (!latest) return false;
    }
    const blockers = latest?.preflight?.blockers || [];
    if (blockers.length > 0) {
      return window.confirm(
        `Pre-Flight hat offene Blocker gefunden:\n\n• ${blockers.join('\n• ')}\n\nTrotzdem in den Bewerbungsordner exportieren?`
      );
    }
    return true;
  };

  const handleOpenFolder = async () => {
    const targetJobId = draft?.job_id ?? selectedJobId;
    if (!targetJobId) return;
    if (!(await ensureLetterCompiledAndChecked())) return;
    try {
      const res = await api.exportDossier(targetJobId, draft?.lang || 'auto', true, true);
      const warnSuffix = res.warnings?.length ? ` · Hinweise: ${res.warnings.join(' | ')}` : '';
      onNotify(`Bewerbungsordner geöffnet (${res.file_count} Dateien): ${res.folder}${warnSuffix}`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Ordner konnte nicht geöffnet werden');
    }
  };

  const handleDelete = async () => {
    if (!draft?.id) return;
    if (!window.confirm('Diesen Anschreiben-Entwurf wirklich löschen?')) return;
    setBusy(true);
    try {
      await api.deleteLetter(draft.id);
      const remaining = await refreshLettersList();
      if (remaining.length > 0 && remaining[0].id) {
        const nextId = remaining[0].id;
        onSelectLetter(nextId);
        const loaded = await api.getLetter(nextId);
        setDraft(loaded);
        lastLoadedLetterIdRef.current = loaded.id ?? null;
        onSelectJob?.(loaded.job_id ?? null);
        setPdfNonce(Date.now());
      } else {
        const blank = await api.prefillLetter(null, 'de');
        setDraft(blank);
        lastLoadedLetterIdRef.current = null;
        onSelectLetter(null);
        onSelectJob?.(null);
        setPdfNonce(Date.now());
      }
      onNotify('Anschreiben-Entwurf gelöscht.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Löschen fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  };

  const handleCompileRef = useRef(handleCompile);
  handleCompileRef.current = handleCompile;

  useEffect(() => {
    if (!isActive) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isBodyTextModalOpen) {
        e.preventDefault();
        setIsBodyTextModalOpen(false);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'Enter' || e.key.toLowerCase() === 's')) {
        e.preventDefault();
        void handleCompileRef.current();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isActive, isBodyTextModalOpen]);

  const paragraphCount = draft?.body_text
    ? draft.body_text
        .trim()
        .split(/\n\s*\n/)
        .filter((p) => p.trim().length > 0).length
    : 0;
  const wordCount = draft?.body_text?.trim() ? draft.body_text.trim().split(/\s+/).length : 0;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {linkedJob && (
        <div className="studio-flow-bar">
          <div className="flow-target-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {onBackToRadar && (
              <button
                type="button"
                className="btn"
                onClick={() => onBackToRadar(linkedJob.id)}
                title="Zurück zum Radar für diese Stelle"
              >
                <ArrowLeft size={13} strokeWidth={2} />
                <span>Zurück zum Radar</span>
              </button>
            )}
            <span>Aktive Ziel-Stelle:</span>
            <span className="flow-target-company">{linkedJob.company}</span>
            <span>— {linkedJob.title}</span>

            {pipelineJobs.length > 1 && (
              <select
                className="select-compact"
                style={{
                  fontSize: '0.72rem',
                  padding: '2px 8px',
                  maxWidth: '240px',
                  textOverflow: 'ellipsis',
                  background: 'var(--bg-sheet)',
                  border: '1px solid var(--line-hairline)',
                  borderRadius: '4px',
                  color: 'var(--ink-secondary)',
                }}
                value={linkedJob.id}
                aria-label="Aktive Ziel-Stelle wechseln"
                title="Ziel-Stelle wechseln: Lädt vorhandenes Anschreiben oder füllt ein neues aus"
                onChange={async (e) => {
                  const jid = Number(e.target.value);
                  if (!jid || jid === linkedJob.id) return;
                  await flushDirtyLetterDraft();
                  const reqId = ++loadReqIdRef.current;
                  skipNextInitReloadRef.current = true;
                  onSelectJob?.(jid);
                  const matching = letters.find((l) => l.job_id === jid);
                  if (matching && matching.id) {
                    onSelectLetter(matching.id);
                    const loaded = await api.getLetter(matching.id);
                    if (reqId !== loadReqIdRef.current) return;
                    isDirtyRef.current = false;
                    setDraft(loaded);
                    lastLoadedLetterIdRef.current = loaded.id ?? null;
                    setPdfNonce(Date.now());
                    onNotify(`Anschreiben für ${matching.company || 'Stelle'} geladen.`);
                  } else {
                    setBusy(true);
                    try {
                      const prefilled = await api.prefillLetter(jid, draft?.lang || 'de');
                      if (reqId !== loadReqIdRef.current) return;
                      isDirtyRef.current = false;
                      setDraft(prefilled);
                      lastLoadedLetterIdRef.current = prefilled.id ?? null;
                      if (prefilled.id) onSelectLetter(prefilled.id);
                      setPdfNonce(Date.now());
                      onNotify(`Neues Anschreiben für ${prefilled.company || 'Stelle'} vorausgefüllt.`);
                    } catch (err) {
                      if (reqId === loadReqIdRef.current) {
                        onNotify(err instanceof Error ? err.message : 'Vorausfüllen fehlgeschlagen');
                      }
                    } finally {
                      if (reqId === loadReqIdRef.current) setBusy(false);
                    }
                  }
                }}
              >
                {!pipelineJobs.some((j) => j.id === linkedJob.id) && (
                  <option value={linkedJob.id}>
                    {linkedJob.company} — {linkedJob.title}
                  </option>
                )}
                {pipelineJobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.company} — {j.title}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {onOpenCvStudio && (
              <button
                type="button"
                className="btn"
                onClick={() => onOpenCvStudio(linkedJob.id)}
                title="Zum zugehörigen Lebenslauf für diese Stelle wechseln"
              >
                <FileText size={13} strokeWidth={1.85} />
                <span>Zum Lebenslauf →</span>
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={async () => {
                if (!(await ensureLetterCompiledAndChecked())) return;
                try {
                  await api.patchJob(linkedJob.id, { status: 'vorbereiten' });
                  const res = await api.exportDossier(linkedJob.id, draft?.lang || 'auto', true, true);
                  void loadParagraphs();
                  const warnSuffix = res.warnings?.length ? ` · Hinweise: ${res.warnings.join(' | ')}` : '';
                  onNotify(`In Pipeline auf „Vorbereiten“ gesetzt & Ordner geöffnet (${res.file_count} Dateien).${warnSuffix}`);
                } catch (err) {
                  onNotify(err instanceof Error ? err.message : 'Fehler beim Abschluss');
                }
              }}
              title="Setzt die Stelle in der Pipeline auf 'Vorbereiten', exportiert Lebenslauf, Anschreiben & Zeugnisse und öffnet den Explorer-Ordner"
            >
              <CheckCircle2 size={13} strokeWidth={2} />
              <span>In Pipeline auf „Vorbereiten“ &amp; Ordner öffnen</span>
            </button>
          </div>
        </div>
      )}

      <div className={`studio-workspace layout-${layoutPreset}`}>
        {/* Left: Live DIN A4 PDF Preview */}
        <section className="studio-pdf-pane" aria-label="Live Anschreiben PDF-Vorschau">
          <div className="studio-toolbar">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', minWidth: 0 }}>
              <select
                className="select-compact"
                style={{ minWidth: '170px', maxWidth: '340px', flex: '1 1 170px', textOverflow: 'ellipsis' }}
                aria-label="Gespeichertes Anschreiben wählen"
                value={draft?.id ?? ''}
                onChange={async (e) => {
                  const val = e.target.value;
                  if (!val) {
                    void handleNewBlank();
                  } else {
                    const id = Number(val);
                    await flushDirtyLetterDraft();
                    const reqId = ++loadReqIdRef.current;
                    skipNextInitReloadRef.current = true;
                    onSelectLetter(id);
                    try {
                      setBusy(true);
                      const loaded = await api.getLetter(id);
                      if (reqId !== loadReqIdRef.current) return;
                      isDirtyRef.current = false;
                      setDraft(loaded);
                      lastLoadedLetterIdRef.current = loaded.id ?? null;
                      onSelectJob?.(loaded.job_id ?? null);
                      setPdfNonce(Date.now());
                    } catch (err) {
                      if (reqId === loadReqIdRef.current) {
                        onNotify(err instanceof Error ? err.message : 'Anschreiben konnte nicht geladen werden');
                      }
                    } finally {
                      if (reqId === loadReqIdRef.current) setBusy(false);
                    }
                  }
                }}
              >
                {draft?.id == null && <option value="">Ungespeicherter Entwurf</option>}
                {letters.map((lt) => (
                  <option key={lt.id ?? lt.name} value={lt.id ?? ''}>
                    {lt.company ? `${lt.company} — ${lt.subject || lt.name}` : lt.name}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="btn"
                onClick={() => void handleNewBlank()}
                disabled={busy}
                title="Erstellt einen neuen, leeren Anschreiben-Entwurf"
              >
                <FilePlus2 size={13} strokeWidth={1.75} />
                + Neues Anschreiben
              </button>

              {selectedJobId && (
                <button
                  type="button"
                  className="btn"
                  onClick={() => void handlePrefillFromSelectedJob()}
                  disabled={busy}
                  title={
                    linkedJob
                      ? `Firma, Ort und Betreff aus „${linkedJob.company} — ${linkedJob.title}“ übernehmen`
                      : 'Firma, Ort und Betreff aus der im Stellen-Radar markierten Stelle übernehmen'
                  }
                >
                  <Briefcase size={13} strokeWidth={1.75} />
                  Aus Stelle
                </button>
              )}

              {(draft?.job_id || selectedJobId) && letters.some((lt) => lt.id && lt.id !== draft?.id) && (
                <select
                  className="select-compact"
                  style={{ maxWidth: '185px' }}
                  value=""
                  disabled={busy}
                  aria-label="Aus altem Brief klonen"
                  title="Text & Struktur aus einem bisherigen Anschreiben für diese Stelle klonen und Firma/Datum automatisch ersetzen"
                  onChange={(e) => {
                    const srcId = Number(e.target.value);
                    if (srcId) void handleCloneFromTemplate(srcId);
                  }}
                >
                  <option value="">📋 Aus altem Brief klonen …</option>
                  {letters
                    .filter((lt) => lt.id && lt.id !== draft?.id)
                    .map((lt) => (
                      <option key={`tpl-${lt.id}`} value={lt.id ?? ''}>
                        {lt.company ? `${lt.company} (${lt.lang.toUpperCase()})` : lt.name}
                      </option>
                    ))}
                </select>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
              <div className="view-mode-switch" role="group" aria-label="Ansichtsgröße">
                <button
                  type="button"
                  className={`view-mode-btn ${viewMode === 'width' ? 'is-active' : ''}`}
                  onClick={() => setViewMode('width')}
                  title="Breite füllen (optimal zum Lesen)"
                >
                  <Maximize2 size={12} strokeWidth={2} />
                  <span>Breite</span>
                </button>
                <button
                  type="button"
                  className={`view-mode-btn ${viewMode === 'page' ? 'is-active' : ''}`}
                  onClick={() => setViewMode('page')}
                  title="Ganze DIN A4 Seite einpassen (Überblick)"
                >
                  <Minimize2 size={12} strokeWidth={2} />
                  <span>Seite</span>
                </button>
              </div>

              <div className="view-mode-switch" role="group" aria-label="Spaltenaufteilung">
                <button
                  type="button"
                  className={`view-mode-btn ${layoutPreset === 'preview' ? 'is-active' : ''}`}
                  onClick={() => setLayoutPreset('preview')}
                  title="Fokus PDF-Vorschau (Groß)"
                >
                  <span>Fokus PDF</span>
                </button>
                <button
                  type="button"
                  className={`view-mode-btn ${layoutPreset === 'balanced' ? 'is-active' : ''}`}
                  onClick={() => setLayoutPreset('balanced')}
                  title="Ausgeglichen (50 / 50)"
                >
                  <span>50/50</span>
                </button>
                <button
                  type="button"
                  className={`view-mode-btn ${layoutPreset === 'editor' ? 'is-active' : ''}`}
                  onClick={() => setLayoutPreset('editor')}
                  title="Fokus Editor (Schreiben)"
                >
                  <span>Fokus Text</span>
                </button>
              </div>

              {draft?.pdf_ready ? (
                <span className={`stamp ${draft.pages === 1 ? 'stamp-fresh' : 'stamp-warn'}`}>
                  {draft.pages} / 1 Seite {draft.pages === 1 ? 'OK' : 'Kürzen!'}
                </span>
              ) : (
                <span className="stamp stamp-relisted">Entwurf</span>
              )}

              {draft?.pages != null && draft.pages > 1 && (
                <button
                  type="button"
                  className="btn-shrink"
                  disabled={busy || aiBusy}
                  onClick={() => {
                    const inst =
                      'Kürze den aktuellen Anschreibentext sprachlich prägnant und geschmeidig um ca. 15-20%, sodass er garantiert auf genau 1 DIN-A4-Seite passt, ohne relevante Stationen oder Kernbotschaften zu verlieren.';
                    setAiInstruction(inst);
                    void handleAiGenerate(inst);
                  }}
                  title="Beauftragt Gemini, den Text um ~15% zu straffen, damit er exakt auf 1 DIN A4-Seite passt"
                >
                  <Zap size={11} strokeWidth={2.2} />
                  <span>Auf 1 Seite straffen</span>
                </button>
              )}

            {draft?.id != null && (
              <button
                type="button"
                className="btn"
                onClick={() => void handleDelete()}
                disabled={busy}
                title="Diesen Anschreiben-Entwurf löschen"
                aria-label="Anschreiben löschen"
              >
                <Trash2 size={13} strokeWidth={1.75} />
              </button>
            )}

            {draft?.id != null && draft.pdf_ready && (
              <a className="btn btn-primary" href={`/api/letters/${draft.id}/pdf?download=true&t=${pdfNonce}`}>
                <Download size={13} strokeWidth={1.75} />
                PDF herunterladen
              </a>
            )}

            {(draft?.job_id || selectedJobId) && (
              <button
                type="button"
                className="btn"
                onClick={() => void handleOpenFolder()}
                title="Speichert Lebenslauf, Anschreiben & Zeugnisse im konfigurierten Export-Ordner und öffnet den Explorer"
              >
                <FolderOpen size={13} strokeWidth={1.75} />
                Ordner öffnen
              </button>
            )}
          </div>
        </div>

        {draft && (
          <div
            className={`preflight-box ${
              draft.preflight?.blockers?.length
                ? 'is-blocked'
                : draft.preflight?.warnings?.length || draft.pdf_stale || isDirtyRef.current || !draft.pdf_ready
                ? 'is-warn'
                : 'is-ok'
            }`}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
              <strong style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                {draft.preflight?.blockers?.length ? (
                  <>
                    <AlertCircle size={13} />
                    <span>Pre-Flight Absende-Gate: Blocker prüfen ({draft.preflight.blockers.length})</span>
                  </>
                ) : draft.pdf_stale || isDirtyRef.current ? (
                  <>
                    <AlertTriangle size={13} />
                    <span>Entwurf geändert — PDF veraltet (bitte rechts „PDF generieren“ klicken)</span>
                  </>
                ) : draft.preflight?.warnings?.length ? (
                  <>
                    <AlertTriangle size={13} />
                    <span>Pre-Flight Hinweise ({draft.preflight.warnings.length})</span>
                  </>
                ) : draft.pdf_ready && draft.preflight?.ok ? (
                  <>
                    <CheckCircle2 size={13} />
                    <span>Pre-Flight bestanden — bereit zum Versenden</span>
                  </>
                ) : (
                  <>
                    <CopyPlus size={13} />
                    <span>Pre-Flight bereit — noch kein PDF generiert</span>
                  </>
                )}
              </strong>
            </div>

            {draft.preflight?.blockers && draft.preflight.blockers.length > 0 && (
              <ul style={{ margin: '4px 0 0', paddingLeft: '16px' }}>
                {draft.preflight.blockers.map((b, i) => (
                  <li key={`blk-${i}`}>{b}</li>
                ))}
              </ul>
            )}

            {draft.preflight?.warnings && draft.preflight.warnings.length > 0 && (
              <ul style={{ margin: '4px 0 0', paddingLeft: '16px' }}>
                {draft.preflight.warnings.map((w, i) => (
                  <li key={`wrn-${i}`}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {draft?.verification && (
          <div
            style={{
              padding: '6px 12px',
              background: 'var(--bg-card)',
              borderBottom: '1px solid var(--border-subtle)',
              fontSize: '11px',
              display: 'flex',
              flexDirection: 'column',
              gap: '4px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    background:
                      draft.verification.status === 'ok'
                        ? '#10b981'
                        : draft.verification.status === 'warn'
                        ? '#f59e0b'
                        : '#ef4444',
                  }}
                />
                <span>ATS- &amp; Format-Gate</span>
              </div>

              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {draft.verification.checks.map((chk) => (
                  <span
                    key={chk.id}
                    style={{
                      padding: '1px 6px',
                      borderRadius: '4px',
                      background: chk.ok ? 'rgba(16, 185, 129, 0.12)' : 'rgba(245, 158, 11, 0.15)',
                      color: chk.ok ? '#059669' : '#d97706',
                      border: `1px solid ${chk.ok ? 'rgba(16, 185, 129, 0.3)' : 'rgba(245, 158, 11, 0.35)'}`,
                      fontSize: '10.5px',
                      fontWeight: 500,
                    }}
                  >
                    {chk.ok ? '✓' : '⚠️'} {chk.label}
                  </span>
                ))}
                {draft.verification.keyword_coverage && (
                  <span
                    style={{
                      padding: '1px 6px',
                      borderRadius: '4px',
                      background: 'rgba(59, 130, 246, 0.12)',
                      color: '#2563eb',
                      border: '1px solid rgba(59, 130, 246, 0.3)',
                      fontSize: '10.5px',
                      fontWeight: 600,
                    }}
                  >
                    🎯 Keyword-Match: {draft.verification.keyword_coverage.coverage_pct}%
                  </span>
                )}
              </div>
            </div>

            {draft.verification.issues.length > 0 && (
              <div style={{ color: '#d97706', fontSize: '10.5px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {draft.verification.issues.map((iss, i) => (
                  <div key={i}>⚠️ {iss.message}</div>
                ))}
              </div>
            )}

            {draft.verification.keyword_coverage?.missing && draft.verification.keyword_coverage.missing.length > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap', color: 'var(--ink-secondary)', fontSize: '10px' }}>
                <span>Keywords aus Ausschreibung:</span>
                {draft.verification.keyword_coverage.missing.slice(0, 6).map((kw, i) => (
                  <span
                    key={i}
                    style={{
                      padding: '1px 4px',
                      background: 'var(--bg-subtle)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '3px',
                    }}
                  >
                    {kw}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className={`pdf-mat is-fit-${viewMode}`}>
          {busy && (
            <div className="pdf-compile-overlay" aria-live="polite">
              <RefreshCw size={13} className="spin-icon" strokeWidth={2.2} />
              <span>Setze LaTeX-PDF …</span>
            </div>
          )}
          {draft?.id != null && draft.pdf_ready ? (
            Array.from({ length: Math.max(1, draft.pages || 1) }).map((_, pageIdx) => (
              <img
                key={`${pageIdx}-${pdfNonce}`}
                alt={`DIN Anschreiben Seite ${pageIdx + 1}`}
                className="a4-sheet-img"
                src={`/api/letters/${draft.id}/preview?page=${pageIdx}&v=${pdfNonce}`}
              />
            ))
          ) : (
            <div className="a4-sheet-draft" aria-label="DIN A4 Briefbogen Entwurfsvorschau">
              <div>
                <div className="a4-draft-header">
                  <span className="a4-draft-name">ABSENDER-VORSCHAU</span>
                  <span className="a4-draft-tag">DIN A4 Vorschau · 210 × 297 mm</span>
                </div>

                <div className="a4-draft-recipient">
                  <div style={{ fontWeight: 700, color: '#0F172A' }}>
                    {draft?.company || 'Empfänger-Unternehmen'}
                  </div>
                  {draft?.contact_person && <div>{draft.contact_person}</div>}
                  <div>{draft?.street || 'Straße & Hausnummer'}</div>
                  <div>{draft?.zip_city || 'PLZ & Ort'}</div>
                </div>

                <div className="a4-draft-date">
                  {draft?.place || 'München'}
                  {draft?.date_str ? `, ${draft.date_str}` : ''}
                </div>

                <div className="a4-draft-subject">
                  {draft?.subject || 'Betreffzeile des Anschreibens'}
                </div>
              </div>

              <div className="a4-draft-body">
                <div style={{ fontWeight: 600, color: '#1E293B' }}>
                  {draft?.salutation || 'Sehr geehrte Damen und Herren,'}
                </div>

                {draft?.body_text?.trim() ? (
                  draft.body_text
                    .trim()
                    .split(/\n\s*\n/)
                    .slice(0, 5)
                    .map((para, idx) => (
                      <p key={idx} style={{ margin: 0, color: '#1E293B' }}>
                        {para}
                      </p>
                    ))
                ) : (
                  <div className="a4-draft-hint">
                    <Mail size={22} strokeWidth={1.7} color="#394C5E" />
                    <strong style={{ color: '#0F172A', fontSize: '0.8rem' }}>
                      Bereit für einseitigen LaTeX-PDF-Satz (DIN A4)
                    </strong>
                    <p style={{ fontSize: '0.73rem', maxWidth: '38ch', lineHeight: 1.45, color: '#475569' }}>
                      Trage rechts Empfängeradresse und deinen Brieftext ein und klicke auf{' '}
                      <strong style={{ color: '#0F172A' }}>PDF generieren</strong> (oder drücke{' '}
                      <span className="num" style={{ fontWeight: 600 }}>Strg + Enter</span>).
                    </p>
                  </div>
                )}
              </div>

              <div className="a4-draft-footer">
                <div>{draft?.closing || 'Mit freundlichen Grüßen'}</div>
                <div style={{ fontWeight: 700, color: '#394C5E' }}>Alex Muster</div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Right: Clean Structured Recipient & Body Editor (Zero LLM bloat) */}
      <section className="studio-editor-pane" aria-label="Anschreiben Formular">
        <div className="studio-toolbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <button
              type="button"
              className={`seg-btn ${draft?.lang === 'de' ? 'is-active' : ''}`}
              onClick={() => handleSwitchLanguage('de')}
            >
              Deutsch (DE)
            </button>
            <button
              type="button"
              className={`seg-btn ${draft?.lang === 'en' ? 'is-active' : ''}`}
              onClick={() => handleSwitchLanguage('en')}
            >
              English (EN)
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isSavingDraft ? (
              <span
                style={{
                  fontSize: '11px',
                  color: 'var(--text-muted)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <RefreshCw size={11} className="spin-icon" /> Speichert Entwurf …
              </span>
            ) : draftSavedAt ? (
              <span
                style={{
                  fontSize: '11px',
                  color: 'var(--green, #10b981)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
                title={`Entwurf automatisch um ${draftSavedAt} Uhr in SQLite gesichert`}
              >
                <CheckCircle2 size={11} /> Entwurf gesichert ({draftSavedAt})
              </span>
            ) : null}

            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !draft}
              onClick={() => void handleCompile()}
            >
              <RefreshCw size={13} strokeWidth={1.75} />
              {busy ? 'Setze LaTeX-PDF …' : 'PDF generieren & aktualisieren'}
            </button>
          </div>
        </div>

        {draft && (
          <div className="studio-scroll">
            {/* Active Job Context Banner & Collapsible Cheat Sheet */}
            {linkedJob && (
              <div style={{ marginBottom: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '7px 12px', background: 'var(--bg-subtle-tint)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    Ziel-Stelle: <strong style={{ color: 'var(--text-primary)' }}>{linkedJob.company}</strong> — {linkedJob.title}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                    <button
                      type="button"
                      className="btn-xs"
                      onClick={() => setShowJobCheatSheet((v) => !v)}
                      title="Erkannte Anforderungen, Stärken und Lücken dieser Stelle ein-/ausblenden"
                    >
                      <span>Spickzettel</span>
                      {showJobCheatSheet ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                    </button>
                    <span className="score-pill band-good">Score {linkedJob.rank_score}</span>
                    <a href={linkedJob.url} target="_blank" rel="noreferrer" className="btn" title="Originalanzeige öffnen">
                      <ExternalLink size={12} />
                    </a>
                  </div>
                </div>

                {showJobCheatSheet && (
                  <div className="cheatsheet-panel">
                    {linkedJob.ai_parsed ? (
                      <>
                        {linkedJob.ai_parsed.evidence?.length > 0 && (
                          <div>
                            <strong>✓ Stärken für diese Stelle:</strong>
                            <ul>
                              {linkedJob.ai_parsed.evidence.slice(0, 4).map((ev, i) => (
                                <li key={`ev-${i}`}>{ev}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {linkedJob.ai_parsed.gaps?.length > 0 && (
                          <div>
                            <strong>⚠️ Offene Punkte / gezielt adressieren:</strong>
                            <ul>
                              {linkedJob.ai_parsed.gaps.slice(0, 4).map((gp, i) => (
                                <li key={`gp-${i}`}>{gp}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {linkedJob.ai_deep_review?.requirements && linkedJob.ai_deep_review.requirements.length > 0 && (
                          <div>
                            <strong>🎯 Top-Anforderungen (Tiefenmatrix):</strong>
                            <ul>
                              {linkedJob.ai_deep_review.requirements.slice(0, 4).map((req, i) => (
                                <li key={`rq-${i}`}>
                                  [{req.importance === 'must' ? 'Pflicht' : 'Wunsch'}] {req.requirement}
                                  {req.profile_evidence ? ` → ${req.profile_evidence}` : ''}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </>
                    ) : (
                      <div>
                        <strong>Regel-Treffer:</strong> {linkedJob.rule_reason || 'Noch keine Hydrogen-Analyse vorhanden.'}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* AI Cover Letter Draft Assistant (Argon vs Hydrogen) */}
            <div className="ai-assistant-box">
              <div className="ai-assistant-head">
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                  <Sparkles size={16} strokeWidth={1.9} color="var(--accent-blue)" />
                  <strong style={{ fontSize: 'var(--text-sm)' }}>KI-Entwurfsassistent (1-Seiten DIN-Anschreiben)</strong>
                </div>

                <div className="model-switch-group" role="radiogroup" aria-label="KI-Modell wählen">
                  <button
                    type="button"
                    className={`model-switch-btn ${aiModel === 'hydrogen' ? 'is-active' : ''}`}
                    onClick={() => setAiModel('hydrogen')}
                    title="Gemini 4 Hydrogen — Schnell & präzise über dein Google-Abo"
                  >
                    <Cpu size={12} strokeWidth={2} />
                    <span>Gemini 4 Hydrogen</span>
                  </button>
                  <button
                    type="button"
                    className={`model-switch-btn is-argon ${aiModel === 'argon' ? 'is-active' : ''}`}
                    onClick={() => setAiModel('argon')}
                    title="Gemini 4 Argon — Höchste sprachliche Qualität & Tiefen-Formulierung"
                  >
                    <Sparkles size={12} strokeWidth={2} />
                    <span>Argon</span>
                  </button>
                  <button
                    type="button"
                    className={`model-switch-btn is-neon ${aiModel === 'neon' ? 'is-active' : ''}`}
                    onClick={() => setAiModel('neon')}
                    title="Gemini 4 Neon — Neueste Reasoning- & Formulierungstiefe"
                  >
                    <Sparkles size={12} strokeWidth={2} />
                    <span>Neon</span>
                  </button>
                </div>
              </div>

              <div className="prompt-chips">
                {LETTER_AI_PRESETS.map((preset, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="chip-btn"
                    title={preset.text}
                    onClick={() => setAiInstruction(preset.text)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <input
                  type="text"
                  className="field-input"
                  style={{ flex: 1, minWidth: '240px' }}
                  placeholder={
                    linkedJob
                      ? `Anweisung für ${linkedJob.company} (leer lassen = kompletter Entwurf passend zur Stelle) …`
                      : 'Wunsch-Fokus für das Anschreiben eingeben …'
                  }
                  value={aiInstruction}
                  onChange={(e) => setAiInstruction(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      void handleAiGenerate();
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={aiBusy || busy}
                  onClick={() => void handleAiGenerate()}
                  title="Erstellt ein auf das Unternehmen zugeschnittenes Anschreiben mit Fokus auf Problemlösung & Zukunft"
                >
                  <Sparkles size={13} strokeWidth={1.85} />
                  {aiBusy
                    ? `${aiModel === 'neon' ? 'Neon' : aiModel === 'argon' ? 'Argon' : 'Hydrogen'} schreibt & setzt PDF …`
                    : `Mit ${aiModel === 'neon' ? 'Neon' : aiModel === 'argon' ? 'Argon' : 'Hydrogen'} Entwurf erstellen`}
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{
                    borderColor: 'rgba(16, 185, 129, 0.4)',
                    background: 'rgba(16, 185, 129, 0.08)',
                    color: 'var(--ink-primary)',
                    fontWeight: 600,
                  }}
                  disabled={aiBusy || busy || !draft?.body_text?.trim()}
                  onClick={() => void handleAiGenerate(undefined, true)}
                  title="Nur Rechtschreibung, Grammatik und Kommas korrigieren. Behält deinen Text, Inhalt und persönlichen Schreibstil 100% unverändert!"
                >
                  <CheckCircle2 size={13} strokeWidth={2} color="#10b981" />
                  <span>Nur Korrektur (Inhalt beibehalten)</span>
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{
                    borderColor: 'rgba(99, 102, 241, 0.45)',
                    background: 'rgba(99, 102, 241, 0.08)',
                    color: 'var(--ink-primary)',
                    fontWeight: 600,
                  }}
                  disabled={aiBusy || busy || !draft?.body_text?.trim()}
                  onClick={() => void handleAiGenerate(undefined, false, true)}
                  title="Humanize (nach blader/humanizer): Entfernt KI-Muster (Triaden, 'Nicht nur X sondern Y', Hype-Wörter, monotone Satzstrukturen) und macht den Text 100% menschlich, direkt & authentisch."
                >
                  <Wand2 size={13} strokeWidth={2} color="#6366f1" />
                  <span>Humanize (blader/humanizer)</span>
                </button>
              </div>

              {aiSummary && (
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--badge-fresh-fg)', fontWeight: 500, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <div>✓ {aiSummary}</div>
                  {removedPatterns.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center', marginTop: '2px' }}>
                      <span style={{ fontSize: '11px', color: 'var(--ink-muted)' }}>Bereinigte KI-Muster:</span>
                      {removedPatterns.map((pat, idx) => (
                        <span
                          key={idx}
                          style={{
                            fontSize: '10.5px',
                            background: 'rgba(99, 102, 241, 0.12)',
                            color: '#4f46e5',
                            border: '1px solid rgba(99, 102, 241, 0.25)',
                            padding: '1px 6px',
                            borderRadius: '4px',
                            fontWeight: 500,
                          }}
                        >
                          ✕ {pat}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {auditWarnings.length > 0 && (
              <div
                style={{
                  padding: '10px 14px',
                  background: 'rgba(245, 158, 11, 0.1)',
                  border: '1px solid rgba(245, 158, 11, 0.35)',
                  borderRadius: '6px',
                  fontSize: '11.5px',
                  color: '#b45309',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px', fontWeight: 600 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>⚠️</span>
                    <span>Stil- &amp; Fakten-Audit (ai-job-search Best-Practice)</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setAuditWarnings([])}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#b45309', fontSize: '14px', lineHeight: 1 }}
                  >
                    ×
                  </button>
                </div>
                <ul style={{ margin: 0, paddingLeft: '16px', lineHeight: 1.5 }}>
                  {auditWarnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* 1. Empfänger & Unternehmen Card */}
            <div className="editor-card">
              <div className="editor-card-header">
                <div className="editor-card-title-group">
                  <Building2 size={16} className="editor-card-icon" />
                  <div>
                    <div className="editor-card-title">Empfänger &amp; Unternehmen</div>
                    <div className="editor-card-subtitle">Anschriftenzone nach DIN 5008 für Fensterbriefumschläge</div>
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: 'auto' }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={autofillingAddress || busy}
                    onClick={() => void handleAutofillAddress()}
                    title="Ermittelt Unternehmensanschrift, Straße, PLZ, Ansprechpartner und Stellen-ID automatisch"
                    style={{ fontSize: '11.5px', padding: '5px 10px', display: 'inline-flex', alignItems: 'center', gap: '5px' }}
                  >
                    <Wand2 size={12} strokeWidth={2} className={autofillingAddress ? 'animate-spin' : ''} />
                    {autofillingAddress ? 'Ermittle Anschrift …' : 'Automatisch ausfüllen'}
                  </button>
                </div>
              </div>

              {draft.address_source && (
                <div className="address-source-strip">
                  <div className="address-source-meta">
                    <span className="address-source-pill">
                      Quelle: {draft.address_source.label}
                    </span>
                    {draft.address_source.snippet && (
                      <span className="address-source-snippet" title={draft.address_source.snippet}>
                        „{draft.address_source.snippet}“
                      </span>
                    )}
                  </div>
                  <div className="address-source-actions">
                    {draft.address_source.url && (
                      <a
                        href={draft.address_source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-secondary address-source-btn"
                        title="Original-Ausschreibung oder Impressum im Browser öffnen"
                      >
                        <ExternalLink size={11} strokeWidth={2} />
                        Quelle öffnen
                      </a>
                    )}
                    {draft.address_source.verification_url && (
                      <a
                        href={draft.address_source.verification_url}
                        target="_blank"
                        rel="noreferrer"
                        className="btn address-source-btn"
                        title="Auf Google Maps validieren"
                      >
                        <MapPin size={11} strokeWidth={2} />
                        Maps prüfen
                      </a>
                    )}
                  </div>
                </div>
              )}
              <div className="editor-card-body">
                <div className="form-grid-2">
                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-company">
                      Unternehmen
                    </label>
                    <input
                      id="lt-company"
                      className="field-input"
                      placeholder="z. B. MBDA Deutschland GmbH"
                      value={draft.company}
                      onChange={(e) => updateDraftField('company', e.target.value)}
                    />
                  </div>

                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-contact">
                      Ansprechpartner / Abteilung (optional)
                    </label>
                    <input
                      id="lt-contact"
                      className="field-input"
                      placeholder="z. B. Personalabteilung / Frau Muster"
                      value={draft.contact_person}
                      onChange={(e) => updateDraftField('contact_person', e.target.value)}
                    />
                  </div>

                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-street">
                      Straße &amp; Hausnummer
                    </label>
                    <input
                      id="lt-street"
                      className="field-input"
                      placeholder="z. B. Hagenauer Forst 27"
                      value={draft.street}
                      onChange={(e) => updateDraftField('street', e.target.value)}
                    />
                  </div>

                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-zip-city">
                      PLZ &amp; Ort
                    </label>
                    <input
                      id="lt-zip-city"
                      className="field-input"
                      placeholder="z. B. 86529 Schrobenhausen"
                      value={draft.zip_city}
                      onChange={(e) => updateDraftField('zip_city', e.target.value)}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 2. Betreff & Formalia Card */}
            <div className="editor-card">
              <div className="editor-card-header">
                <div className="editor-card-title-group">
                  <Calendar size={16} className="editor-card-icon" />
                  <div>
                    <div className="editor-card-title">Betreff &amp; Formalia</div>
                    <div className="editor-card-subtitle">Betreffzeile, Referenz und Absendedaten</div>
                  </div>
                </div>
              </div>
              <div className="editor-card-body">
                <div className="field-group">
                  <label className="field-label" htmlFor="lt-subject">
                    Betreffzeile (ohne „Betreff:“)
                  </label>
                  <input
                    id="lt-subject"
                    className="field-input"
                    placeholder="Bewerbung als AI / Software Engineer (m/w/d)"
                    value={draft.subject}
                    onChange={(e) => updateDraftField('subject', e.target.value)}
                  />
                </div>

                <div className="form-grid-3">
                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-job-ref">
                      Stellen-ID / Referenz (optional)
                    </label>
                    <input
                      id="lt-job-ref"
                      className="field-input mono"
                      placeholder="z. B. JR-10492"
                      value={draft.job_ref}
                      onChange={(e) => updateDraftField('job_ref', e.target.value)}
                    />
                  </div>

                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-place">
                      Absendeort
                    </label>
                    <input
                      id="lt-place"
                      className="field-input"
                      value={draft.place}
                      onChange={(e) => updateDraftField('place', e.target.value)}
                    />
                  </div>

                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-date">
                      Datum
                    </label>
                    <input
                      id="lt-date"
                      className="field-input mono"
                      placeholder="TT.MM.JJJJ"
                      value={draft.date_str}
                      onChange={(e) => updateDraftField('date_str', e.target.value)}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 3. Brieftext & Unterschrift Card */}
            <div className="editor-card">
              <div className="editor-card-header">
                <div className="editor-card-title-group">
                  <PenLine size={16} className="editor-card-icon" />
                  <div>
                    <div className="editor-card-title">Brieftext &amp; Unterschrift</div>
                    <div className="editor-card-subtitle">Anrede, Hauptteil und Signatur (1 DIN-A4-Seite)</div>
                  </div>
                </div>
                <span className="mono" style={{ fontSize: '0.72rem', color: 'var(--ink-muted)' }}>
                  {wordCount} Wörter · {paragraphCount} Absätze
                </span>
              </div>
              <div className="editor-card-body">
                <div className="field-group">
                  <label className="field-label" htmlFor="lt-salutation">
                    Anrede
                  </label>
                  <input
                    id="lt-salutation"
                    className="field-input"
                    value={draft.salutation}
                    onChange={(e) => updateDraftField('salutation', e.target.value)}
                  />
                </div>

                <div className="field-group">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px', marginBottom: '6px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                      <label className="field-label" htmlFor="lt-body" style={{ margin: 0 }}>
                        Brieftext &amp; Absatz-Baukasten
                      </label>
                      <div className="view-mode-switch" role="group" aria-label="Editor-Modus">
                        <button
                          type="button"
                          className={`view-mode-btn ${bodyEditorMode === 'organizer' ? 'is-active' : ''}`}
                          onClick={() => setBodyEditorMode('organizer')}
                          title="Absatz-Baukasten mit Drag & Drop und bewährten Absätzen aus deinen Bewerbungen"
                        >
                          <Layers size={12} strokeWidth={2} />
                          <span>Absatz-Baukasten</span>
                        </button>
                        <button
                          type="button"
                          className={`view-mode-btn ${bodyEditorMode === 'text' ? 'is-active' : ''}`}
                          onClick={() => setBodyEditorMode('text')}
                          title="Klassisches Freitextfeld"
                        >
                          <span>Freitext</span>
                        </button>
                        <button
                          type="button"
                          className={`view-mode-btn ${bodyEditorMode === 'both' ? 'is-active' : ''}`}
                          onClick={() => setBodyEditorMode('both')}
                          title="Beides synchron anzeigen"
                        >
                          <span>Beides</span>
                        </button>
                      </div>

                      {bodyEditorMode !== 'organizer' && (
                        <div className="view-mode-switch" role="group" aria-label="Textfeld-Höhe">
                          <button
                            type="button"
                            className={`view-mode-btn ${bodyTextSize === 'normal' ? 'is-active' : ''}`}
                            onClick={() => setBodyTextSize('normal')}
                            title="Kompakt (12 Zeilen)"
                          >
                            <span>Kompakt</span>
                          </button>
                          <button
                            type="button"
                            className={`view-mode-btn ${bodyTextSize === 'tall' ? 'is-active' : ''}`}
                            onClick={() => setBodyTextSize('tall')}
                            title="Groß (22 Zeilen — empfohlen)"
                          >
                            <span>Groß</span>
                          </button>
                          <button
                            type="button"
                            className={`view-mode-btn ${bodyTextSize === 'auto' ? 'is-active' : ''}`}
                            onClick={() => setBodyTextSize('auto')}
                            title="Volle Höhe (automatisch an Textlänge angepasst, kein Textarea-Scrollbalken)"
                          >
                            <span>Volle Höhe</span>
                          </button>
                        </div>
                      )}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span className="mono" style={{ fontSize: '0.68rem', color: 'var(--ink-muted)' }}>
                        {(draft.body_text || '').trim().split(/\s+/).filter(Boolean).length} Wörter (Richtwert: 220–340)
                      </span>
                      <button
                        type="button"
                        className="btn"
                        style={{
                          padding: '3px 8px',
                          fontSize: '0.72rem',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '5px',
                          background: 'var(--bg-sheet)',
                          borderColor: 'var(--line-strong)',
                        }}
                        onClick={() => setIsBodyTextModalOpen(true)}
                        title="Vollbild-Fokusmodus zum bequemen Schreiben und Bearbeiten öffnen"
                      >
                        <Maximize2 size={12} strokeWidth={2} />
                        <span>Fokus / Großansicht</span>
                      </button>
                    </div>
                  </div>

                  {bodyEditorMode !== 'text' && (
                    <LetterParagraphOrganizer
                      lang={draft.lang}
                      targetCompany={draft.company || linkedJob?.company || ''}
                      bodyText={draft.body_text}
                      onChangeBodyText={(next) => updateDraftField('body_text', next)}
                      paragraphs={paragraphs}
                      loadingParagraphs={loadingParagraphs}
                      linkedJob={linkedJob}
                      recommendations={paragraphRecs}
                      recSummary={recSummary}
                      recBusy={recBusy}
                      adaptBusy={adaptBusy}
                      onRunHydrogenMatch={handleRunHydrogenParagraphMatch}
                      onAdaptBlocksWithHydrogen={handleAdaptBlocksWithHydrogen}
                      onSaveParagraphToLibrary={handleSaveParagraphToLibrary}
                      onDeleteParagraphFromLibrary={handleDeleteParagraphFromLibrary}
                      onNotify={onNotify}
                    />
                  )}

                  {bodyEditorMode !== 'organizer' && (
                    <textarea
                      id="lt-body"
                      className="field-textarea"
                      rows={
                        bodyTextSize === 'normal'
                          ? 12
                          : bodyTextSize === 'tall'
                          ? 22
                          : Math.max(22, (draft.body_text || '').split('\n').length + 3)
                      }
                      style={{
                        marginTop: bodyEditorMode === 'both' ? '12px' : undefined,
                        minHeight:
                          bodyTextSize === 'normal'
                            ? '240px'
                            : bodyTextSize === 'tall'
                            ? '460px'
                            : '620px',
                        resize: 'vertical',
                        lineHeight: '1.6',
                        fontFamily: 'inherit',
                        fontSize: '0.88rem',
                      }}
                      placeholder="Füge hier deinen Anschreibentext ein. Sonderzeichen (%, &, _) werden beim PDF-Satz automatisch für LaTeX maskiert …"
                      value={draft.body_text}
                      onChange={(e) => updateDraftField('body_text', e.target.value)}
                    />
                  )}
                </div>

                <div className="form-grid-2" style={{ alignItems: 'end' }}>
                  <div className="field-group">
                    <label className="field-label" htmlFor="lt-closing">
                      Grußformel
                    </label>
                    <input
                      id="lt-closing"
                      className="field-input"
                      value={draft.closing}
                      onChange={(e) => updateDraftField('closing', e.target.value)}
                    />
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <label
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontSize: 'var(--text-xs)',
                        padding: '9px 12px',
                        border: '1px solid var(--line-strong)',
                        borderRadius: '6px',
                        background: 'var(--bg-sheet)',
                        cursor: 'pointer',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={draft.include_signature}
                        onChange={(e) => updateDraftField('include_signature', e.target.checked)}
                      />
                      <span>Handschriftliche Signatur im PDF einbinden</span>
                    </label>
                    {draft.include_signature && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: 'var(--text-xs)', color: 'var(--ink-secondary)' }}>
                        <span>Signaturbreite:</span>
                        <input
                          type="range"
                          min={2.4}
                          max={6.4}
                          step={0.2}
                          value={draft.signature_width_cm || 4.2}
                          onChange={(e) => updateDraftField('signature_width_cm', parseFloat(e.target.value))}
                          style={{ flex: 1 }}
                          aria-label="Signaturbreite in cm"
                        />
                        <span className="mono">{(draft.signature_width_cm || 4.2).toFixed(1)} cm</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: '4px' }}>
              <span className="mono" style={{ fontSize: '0.72rem', color: 'var(--ink-muted)' }}>
                Tastenkürzel: Strg + S oder Strg + Enter aktualisiert sofort die PDF-Vorschau links
              </span>
            </div>
          </div>
        )}
      </section>
    </div>

    {/* Fullscreen / Focus Mode Modal for Writing Letter Body */}
    {isBodyTextModalOpen && draft && (
      <div
        className="modal-backdrop"
        style={{ zIndex: 1200 }}
        onClick={() => setIsBodyTextModalOpen(false)}
        role="dialog"
        aria-modal="true"
        aria-labelledby="letter-focus-title"
      >
        <div
          className="modal-dialog"
          style={{
            maxWidth: '960px',
            width: '94vw',
            height: '88vh',
            maxHeight: '900px',
            display: 'flex',
            flexDirection: 'column',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="modal-header">
            <div className="modal-title-group">
              <div className="modal-icon-badge" style={{ background: 'linear-gradient(135deg, #4f46e5 0%, #6366f1 100%)' }}>
                <Maximize2 size={18} strokeWidth={2.2} />
              </div>
              <div>
                <div id="letter-focus-title" className="modal-title">Brieftext — Fokus-Editor</div>
                <div className="modal-subtitle">
                  {(draft.body_text || '').trim().split(/\s+/).filter(Boolean).length} Wörter · {(draft.body_text || '').split(/\n\s*\n/).filter((p) => p.trim()).length} Absätze · {(draft.body_text || '').length} Zeichen (Richtwert 1 DIN A4-Seite: 220–340 Wörter)
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <button
                type="button"
                className="btn"
                style={{
                  borderColor: 'rgba(99, 102, 241, 0.45)',
                  background: 'rgba(99, 102, 241, 0.08)',
                  color: 'var(--ink-primary)',
                  fontWeight: 600,
                  fontSize: '0.8rem',
                }}
                disabled={aiBusy || busy || !draft.body_text?.trim()}
                onClick={() => void handleAiGenerate(undefined, false, true)}
                title="Humanize (blader/humanizer): Bereinigt KI-Muster und formuliert den Text menschlich & authentisch"
              >
                <Wand2 size={13} strokeWidth={2} color="#6366f1" />
                <span>Humanize (AI → Mensch)</span>
              </button>
              <button
                type="button"
                className="btn btn-primary"
                style={{ fontSize: '0.8rem' }}
                disabled={busy}
                onClick={() => void handleCompile()}
                title="PDF neu kompilieren & speichern (Strg + S oder Strg + Enter)"
              >
                <Sparkles size={13} strokeWidth={2} />
                <span>PDF kompilieren</span>
              </button>
              <button
                type="button"
                className="btn"
                style={{ fontSize: '0.8rem', padding: '6px 10px' }}
                onClick={() => setIsBodyTextModalOpen(false)}
                title="Fokus-Editor schließen (Esc)"
                aria-label="Fokus-Editor schließen"
              >
                <Minimize2 size={14} strokeWidth={2} />
                <span>Schließen</span>
              </button>
            </div>
          </div>

          <div
            className="modal-body"
            style={{
              flex: 1,
              padding: '16px 20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              background: 'var(--bg-card)',
            }}
          >
            <textarea
              autoFocus
              className="field-textarea"
              style={{
                flex: 1,
                width: '100%',
                height: '100%',
                minHeight: '380px',
                resize: 'none',
                padding: '18px 20px',
                fontSize: '0.96rem',
                lineHeight: '1.65',
                fontFamily: 'inherit',
                borderRadius: '8px',
                border: '1px solid var(--line-strong)',
                background: 'var(--bg-sheet)',
                color: 'var(--ink-primary)',
                boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.15)',
              }}
              placeholder="Füge hier deinen Anschreibentext ein. Absätze einfach durch eine Leerzeile trennen …"
              value={draft.body_text}
              onChange={(e) => updateDraftField('body_text', e.target.value)}
            />
          </div>

          <div className="modal-footer" style={{ padding: '12px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: 'var(--text-xs)', color: 'var(--ink-muted)' }}>
              <span>Tastenkürzel: <kbd style={{ padding: '2px 5px', borderRadius: '4px', background: 'var(--bg-sheet)', border: '1px solid var(--line-hairline)' }}>Strg + S</kbd> zum Kompilieren</span>
              <span>·</span>
              <span><kbd style={{ padding: '2px 5px', borderRadius: '4px', background: 'var(--bg-sheet)', border: '1px solid var(--line-hairline)' }}>Esc</kbd> zum Schließen</span>
              {draft.pdf_ready && (
                <>
                  <span>·</span>
                  <span className={`stamp ${draft.pages === 1 ? 'stamp-fresh' : 'stamp-warn'}`} style={{ fontSize: '0.72rem' }}>
                    {draft.pages} / 1 Seite {draft.pages === 1 ? 'OK' : 'Kürzen!'}
                  </span>
                </>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <button
                type="button"
                className="btn btn-primary"
                style={{ fontSize: '0.84rem' }}
                onClick={() => setIsBodyTextModalOpen(false)}
              >
                Fertig / Schließen
              </button>
            </div>
          </div>
        </div>
      </div>
    )}
    </div>
  );
};
