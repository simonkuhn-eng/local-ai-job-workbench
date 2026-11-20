import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CopyPlus,
  Cpu,
  Download,
  ExternalLink,
  FolderOpen,
  Image as ImageIcon,
  Mail,
  Maximize2,
  Minimize2,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  Zap,
} from 'lucide-react';
import { api } from '../api';
import type { CvData, CvEducation, CvEduBullet, CvExperience, CvVariant, JobDetail } from '../types';

interface CvStudioViewProps {
  isActive: boolean;
  activeVariantId: number;
  selectedJobId: number | null;
  onSelectVariant: (variantId: number) => void;
  onSelectJob?: (jobId: number | null) => void;
  onOpenLetterEditor?: (jobId: number) => void;
  onBusyChange?: (busy: boolean) => void;
  onNotify: (msg: string) => void;
  onBackToRadar?: (jobId: number) => void;
}

const CV_AI_PRESETS = [
  {
    label: '🔍 RAG & Azure Vektorsuche betonen',
    text: 'Betone meine Erfahrung mit RAG, Vektorsuche (Azure AI Search), FastAPI und lokalen LLMs stärker.',
  },
  {
    label: '🤖 Robotik, ROS 2 & BEVFusion',
    text: 'Richte Profil und Stichpunkte gezielt auf Robotik, ROS 2, Sensorfusion und BEVFusion aus.',
  },
  {
    label: '🎨 Layout & Akzentfarbe anpassen',
    text: 'Passe das Design an: Wähle eine elegante dunkelblaue Akzentfarbe (#1e3a8a), optimiere die Spaltenbreite und richte die Abstände harmonisch für exakt eine Seite aus.',
  },
  {
    label: '🎓 Ausbildung vor Berufserfahrung',
    text: 'Ändere die Sektionsreihenfolge: Platziere die Ausbildung vor der Berufserfahrung und stimme das Layout perfekt auf 1 Seite ab.',
  },
  {
    label: '⚡ Auf 1 Seite straffen',
    text: 'Formuliere alle Punkte etwas kompakter und prägnanter, damit das Layout luftig auf 1 Seite bleibt.',
  },
];

export const CvStudioView: React.FC<CvStudioViewProps> = ({
  isActive,
  activeVariantId,
  selectedJobId,
  onSelectVariant,
  onSelectJob,
  onOpenLetterEditor,
  onBusyChange,
  onNotify,
  onBackToRadar,
}) => {
  const [variants, setVariants] = useState<CvVariant[]>([]);
  const [current, setCurrent] = useState<CvVariant | null>(null);
  const [cvDraft, setCvDraft] = useState<CvData | null>(null);
  const [isSavingCvDraft, setIsSavingCvDraft] = useState(false);
  const [cvDraftSavedAt, setCvDraftSavedAt] = useState<string | null>(null);
  const isCvDirtyRef = React.useRef(false);
  const editVersionRef = React.useRef(0);
  const autoSaveTimerRef = React.useRef<number | null>(null);
  const cvDraftRef = React.useRef<{ id: number; data: CvData; name?: string; lang: 'de' | 'en' } | null>(null);
  const variantLoadReqIdRef = React.useRef(0);
  const skipNextVariantReloadRef = React.useRef(false);
  const [lang, setLang] = useState<'de' | 'en'>('de');
  const [rightMode, setRightMode] = useState<'ai' | 'manual'>('ai');
  cvDraftRef.current = current && cvDraft ? { id: current.id, data: cvDraft, name: current.name, lang } : null;

  const [prompt, setPrompt] = useState('');
  const [aiModel, setAiModel] = useState<'hydrogen' | 'argon' | 'neon'>('hydrogen');
  const [useJobContext, setUseJobContext] = useState(true);
  const [linkedJob, setLinkedJob] = useState<JobDetail | null>(null);
  const [showJobCheatSheet, setShowJobCheatSheet] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lastSummary, setLastSummary] = useState('');
  const [pdfNonce, setPdfNonce] = useState(() => Date.now());
  const [viewMode, setViewMode] = useState<'width' | 'page'>('width');
  const [layoutPreset, setLayoutPreset] = useState<'balanced' | 'preview' | 'editor'>('balanced');

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  const loadVariantsList = async () => {
    const list = await api.listCvVariants();
    setVariants(list);
    return list;
  };

  const flushDirtyCvDraft = async () => {
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    if (!isCvDirtyRef.current || !cvDraftRef.current) return;
    const item = cvDraftRef.current;
    const ver = editVersionRef.current;
    try {
      setIsSavingCvDraft(true);
      await api.saveCvVariant(item.id, item.data, item.name, item.lang, false);
      if (editVersionRef.current === ver) {
        isCvDirtyRef.current = false;
        setCvDraftSavedAt(
          new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        );
      }
    } catch (err) {
      console.error('Flush CV draft failed', err);
    } finally {
      setIsSavingCvDraft(false);
    }
  };

  const loadSingleVariant = async (vid: number) => {
    const reqId = ++variantLoadReqIdRef.current;
    await flushDirtyCvDraft();
    setBusy(true);
    try {
      const v = await api.getCvVariant(vid);
      if (reqId !== variantLoadReqIdRef.current) return;
      isCvDirtyRef.current = false;
      setCurrent(v);
      setLang(v.lang === 'en' ? 'en' : 'de');
      if (v.cv_data) {
        setCvDraft(structuredClone(v.cv_data));
      }
      if (v.job_id) {
        onSelectJob?.(v.job_id);
      }
      setPdfNonce(Date.now());
    } catch (err) {
      if (reqId === variantLoadReqIdRef.current) {
        onNotify(err instanceof Error ? err.message : 'CV-Variante konnte nicht geladen werden');
      }
    } finally {
      if (reqId === variantLoadReqIdRef.current) {
        setBusy(false);
      }
    }
  };

  // Sync active variant when selectedJobId changes (switch to job's CV if exists, or Master CV)
  useEffect(() => {
    let cancelled = false;
    const syncWithJob = async () => {
      if (busy) return;
      const list = await api.listCvVariants();
      if (cancelled) return;
      setVariants(list);

      if (selectedJobId) {
        const matching = list.find((v) => v.job_id === selectedJobId);
        if (matching) {
          if (activeVariantId !== matching.id) {
            onSelectVariant(matching.id);
          }
        } else {
          // If the currently viewed variant belongs to another job, fall back to Master CV (id=1)
          const curVar = list.find((v) => v.id === activeVariantId);
          if (curVar && curVar.job_id && curVar.job_id !== selectedJobId) {
            onSelectVariant(1);
          }
        }
      }
    };
    void syncWithJob();
    return () => {
      cancelled = true;
    };
  }, [selectedJobId]);

  useEffect(() => {
    void loadVariantsList();
  }, [activeVariantId]);

  useEffect(() => {
    if (skipNextVariantReloadRef.current) {
      skipNextVariantReloadRef.current = false;
      return;
    }
    void loadSingleVariant(activeVariantId || 1);
  }, [activeVariantId]);

  // Flush dirty draft when switching away from CV tab
  useEffect(() => {
    if (!isActive) {
      void flushDirtyCvDraft();
    }
  }, [isActive]);

  // Debounced auto-save of CV draft without LaTeX recompilation
  useEffect(() => {
    if (!cvDraft || !current || !isCvDirtyRef.current) return;
    const targetVarId = current.id;
    const targetData = cvDraft;
    const targetName = current.name;
    const targetLang = lang;
    const ver = editVersionRef.current;

    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
    }

    autoSaveTimerRef.current = window.setTimeout(async () => {
      try {
        setIsSavingCvDraft(true);
        await api.saveCvVariant(targetVarId, targetData, targetName, targetLang, false);
        if (editVersionRef.current === ver) {
          isCvDirtyRef.current = false;
          setCvDraftSavedAt(
            new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          );
        }
      } catch (err) {
        console.error('Auto-save CV draft failed', err);
      } finally {
        setIsSavingCvDraft(false);
      }
    }, 1500);

    return () => {
      if (autoSaveTimerRef.current) {
        window.clearTimeout(autoSaveTimerRef.current);
      }
    };
  }, [cvDraft, current, lang]);

  // Flush dirty CV draft on unmount
  useEffect(() => {
    return () => {
      if (isCvDirtyRef.current && cvDraftRef.current) {
        const item = cvDraftRef.current;
        void api.saveCvVariant(item.id, item.data, item.name, item.lang, false);
      }
    };
  }, []);

  const handleUpdateCvDraft = (newDraft: CvData) => {
    editVersionRef.current += 1;
    isCvDirtyRef.current = true;
    setCvDraft(newDraft);
  };

  useEffect(() => {
    const targetJobId = current ? current.job_id : selectedJobId;
    if (!targetJobId) {
      setLinkedJob(null);
      return;
    }
    api
      .getJobDetail(targetJobId)
      .then(setLinkedJob)
      .catch(() => setLinkedJob(null));
  }, [current?.job_id, current?.id, selectedJobId]);

  const handleCloneForSelectedJob = async () => {
    if (!selectedJobId && !linkedJob) {
      const created = await api.createCvVariant(`Variante ${new Date().toLocaleDateString('de-DE')}`, null, lang);
      await loadVariantsList();
      onSelectVariant(created.id);
      onNotify(`Neue CV-Variante „${created.name}“ angelegt.`);
      return;
    }
    const targetId = selectedJobId || linkedJob?.id || null;
    const label = linkedJob ? `${linkedJob.company} — ${linkedJob.title.slice(0, 32)}` : `Stelle #${targetId}`;
    setBusy(true);
    try {
      const created = await api.createCvVariant(label, targetId, lang);
      await loadVariantsList();
      onSelectVariant(created.id);
      onNotify(`Eigene CV-Variante für ${label} bereit.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Variante konnte nicht erstellt werden');
    } finally {
      setBusy(false);
    }
  };

  const runAiAdjust = async (instructionText: string, clearPromptOnSuccess = false): Promise<boolean> => {
    if (!current || !instructionText.trim() || busy) return false;
    await flushDirtyCvDraft();
    setBusy(true);
    try {
      let activeId = current.id;
      const targetJobId = useJobContext ? current.job_id ?? selectedJobId : null;
      if (current.job_id === null && targetJobId && useJobContext) {
        // Protect Master CV (id=1): auto-clone a dedicated variant for this job before adjusting!
        const label = linkedJob ? `${linkedJob.company} — ${linkedJob.title}` : `Stelle #${targetJobId}`;
        const cloned = await api.createCvVariant(`CV für ${label}`, targetJobId, lang, current.id);
        activeId = cloned.id;
        skipNextVariantReloadRef.current = true;
        onSelectVariant(cloned.id);
      }
      const res = await api.adjustCvVariant(activeId, instructionText, aiModel, useJobContext, targetJobId);
      isCvDirtyRef.current = false;
      setCurrent(res.variant);
      if (res.variant.cv_data) {
        setCvDraft(structuredClone(res.variant.cv_data));
      }
      setLastSummary(res.summary);
      if (clearPromptOnSuccess) setPrompt('');
      setPdfNonce(Date.now());
      await loadVariantsList();
      if (res.warnings?.length) {
        onNotify(`Hinweis Seitenanzahl: ${res.warnings.join(', ')}`);
      }
      return true;
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'KI-Anpassung fehlgeschlagen');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleAiAdjust = () => runAiAdjust(prompt, true);

  const handleCreatePreset = async (preset: 'ai_backend' | 'robotics_perception') => {
    if (busy) return;
    await flushDirtyCvDraft();
    setBusy(true);
    try {
      const targetId = selectedJobId || linkedJob?.id || null;
      const presetTitle = preset === 'ai_backend' ? 'KI & Backend' : 'Robotik & Perception';
      const baseName = linkedJob
        ? `${linkedJob.company} — ${presetTitle}`
        : `Profil: ${presetTitle}`;
      const created = await api.createCvVariant(baseName, targetId, lang, 1, preset);
      await loadVariantsList();
      onSelectVariant(created.id);
      onNotify(`Preset-Variante „${created.name}“ erstellt.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Preset konnte nicht erstellt werden');
    } finally {
      setBusy(false);
    }
  };

  const handleRenameVariant = async () => {
    if (!current || busy) return;
    const nextName = window.prompt('Neuer Name für diese CV-Variante:', current.name);
    if (!nextName || !nextName.trim() || nextName.trim() === current.name) return;
    setBusy(true);
    try {
      const renamed = await api.renameCvVariant(current.id, nextName.trim());
      setCurrent((prev) => (prev ? { ...prev, name: renamed.name } : renamed));
      await loadVariantsList();
      onNotify(`Variante in „${renamed.name}“ umbenannt.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Umbenennen fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteVariant = async () => {
    if (!current || current.id === 1 || busy) return;
    if (!window.confirm(`CV-Variante „${current.name}“ wirklich löschen?`)) return;
    setBusy(true);
    try {
      await api.deleteCvVariant(current.id);
      const list = await loadVariantsList();
      const fallback = list.find((v) => v.id === 1) || list[0];
      if (fallback) {
        onSelectVariant(fallback.id);
      }
      onNotify('CV-Variante gelöscht.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Löschen fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  };

  const handleManualSave = async (): Promise<boolean> => {
    const cur = current;
    const draftData = cvDraft;
    if (!cur || !draftData || busy) return false;
    if (autoSaveTimerRef.current) {
      window.clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
    const ver = editVersionRef.current;
    setBusy(true);
    try {
      const saved = await api.saveCvVariant(cur.id, draftData, cur.name, lang, true);
      if (editVersionRef.current === ver) {
        isCvDirtyRef.current = false;
        setCvDraftSavedAt(
          new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        );
      }
      setCurrent(saved);
      if (saved.cv_data) setCvDraft(structuredClone(saved.cv_data));
      setPdfNonce(Date.now());
      if (saved.warnings?.length) {
        onNotify(`Lebenslauf gespeichert — Hinweis: ${saved.warnings.join(', ')}`);
      } else {
        onNotify('Lebenslauf neu kompiliert und gespeichert.');
      }
      return true;
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'LaTeX-Kompilierung fehlgeschlagen');
      return false;
    } finally {
      setBusy(false);
    }
  };
  const handleManualSaveRef = React.useRef(handleManualSave);
  handleManualSaveRef.current = handleManualSave;

  const handleUndo = async () => {
    if (!current || busy) return;
    setBusy(true);
    try {
      const restored = await api.undoCvVariant(current.id);
      isCvDirtyRef.current = false;
      setCurrent(restored);
      setLang(restored.lang === 'en' ? 'en' : 'de');
      if (restored.cv_data) setCvDraft(structuredClone(restored.cv_data));
      setPdfNonce(Date.now());
      onNotify('Vorherige CV-Version wiederhergestellt.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Rückgängig nicht möglich');
    } finally {
      setBusy(false);
    }
  };

  const activePages = lang === 'en' ? current?.pages_en ?? 1 : current?.pages_de ?? 1;
  const activeVerification = current
    ? lang === 'en'
      ? current.verification_en ?? current.verification
      : current.verification_de ?? current.verification
    : null;

  useEffect(() => {
    if (!isActive) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void handleManualSaveRef.current();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isActive]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {linkedJob && (
        <div className="studio-flow-bar">
          <div className="flow-target-label">
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
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {onOpenLetterEditor && (
              <button
                type="button"
                className="btn"
                onClick={() => onOpenLetterEditor(linkedJob.id)}
                title="Zum Anschreiben für diese Stelle wechseln"
              >
                <Mail size={13} strokeWidth={1.85} />
                <span>Zum Anschreiben →</span>
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={async () => {
                if (isCvDirtyRef.current) {
                  const ok = await handleManualSave();
                  if (!ok) return;
                }
                try {
                  await api.patchJob(linkedJob.id, { status: 'vorbereiten' });
                  const res = await api.exportDossier(linkedJob.id, lang, true, true);
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
        {/* Left: Crisp Live LaTeX A4 PDF Preview */}
        <section className="studio-pdf-pane" aria-label="Live Lebenslauf PDF-Vorschau">
          <div className="studio-toolbar">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', minWidth: 0 }}>
              <select
                className="select-compact"
                style={{ minWidth: '170px', maxWidth: '340px', flex: '1 1 170px', textOverflow: 'ellipsis' }}
                aria-label="CV-Variante auswählen"
                value={current?.id ?? 1}
                onChange={(e) => onSelectVariant(Number(e.target.value))}
              >
                {variants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.job_id ? `[Stelle] ${v.name}` : v.name}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="btn"
                onClick={() => void handleCloneForSelectedJob()}
                disabled={busy}
                title={
                  linkedJob && current?.job_id !== linkedJob.id
                    ? `Eigene CV-Variante für ${linkedJob.company} klonen`
                    : 'Neue CV-Variante anlegen'
                }
              >
                <CopyPlus size={13} strokeWidth={1.75} />
                {linkedJob && current?.job_id !== linkedJob.id ? 'Für Stelle klonen' : 'Neue Variante'}
              </button>

              <button
                type="button"
                className="btn-xs"
                onClick={() => void handleCreatePreset('ai_backend')}
                disabled={busy}
                title="Erstellt eine vorkonfigurierte Variante mit Schwerpunkt KI, Agentic LLM & Backend (DE/EN)"
              >
                <Sparkles size={11} />
                <span>Preset: KI &amp; Backend</span>
              </button>

              <button
                type="button"
                className="btn-xs"
                onClick={() => void handleCreatePreset('robotics_perception')}
                disabled={busy}
                title="Erstellt eine vorkonfigurierte Variante mit Schwerpunkt Robotik, ROS 2 & Perception (DE/EN)"
              >
                <Cpu size={11} />
                <span>Preset: Robotik &amp; Perception</span>
              </button>

              {current && (
                <button
                  type="button"
                  className="btn"
                  onClick={() => void handleRenameVariant()}
                  disabled={busy}
                  title="Diese CV-Variante umbenennen"
                  aria-label="Variante umbenennen"
                >
                  <Pencil size={13} strokeWidth={1.75} />
                </button>
              )}

              {current && current.id !== 1 && (
                <button
                  type="button"
                  className="btn"
                  onClick={() => void handleDeleteVariant()}
                  disabled={busy}
                  title="Diese CV-Variante löschen (Master-CV bleibt geschützt)"
                  aria-label="Variante löschen"
                >
                  <Trash2 size={13} strokeWidth={1.75} />
                </button>
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

              <span className={`stamp ${activePages === 1 ? 'stamp-fresh' : 'stamp-warn'}`}>
                {activePages} / 1 Seite {activePages === 1 ? 'OK' : 'Kürzen!'}
              </span>

              {activePages > 1 && (
                <button
                  type="button"
                  className="btn-shrink"
                  disabled={busy}
                  onClick={() => {
                    const shrinkPrompt =
                      'Straffe alle Formulierungen, Stichpunkte und Profiltexte kompakt um ca. 10-15%, sodass der gesamte Lebenslauf sauber und luftig auf exakt 1 Seite passt.';
                    setPrompt(shrinkPrompt);
                    void runAiAdjust(shrinkPrompt).then((ok) => {
                      if (ok) onNotify('Lebenslauf auf 1 Seite gestrafft & neu gesetzt.');
                    });
                  }}
                  title="Beauftragt Gemini, Stichpunkte und Profil um ~10-15% zu straffen, damit der Lebenslauf auf 1 Seite passt"
                >
                  <Zap size={11} strokeWidth={2.2} />
                  <span>Auf 1 Seite straffen</span>
                </button>
              )}

            <button
              type="button"
              className={`btn ${lang === 'de' ? 'btn-active-toggle' : ''}`}
              onClick={() => setLang('de')}
            >
              DE
            </button>
            <button
              type="button"
              className={`btn ${lang === 'en' ? 'btn-active-toggle' : ''}`}
              onClick={() => setLang('en')}
            >
              EN
            </button>

            <button
              type="button"
              className="btn"
              disabled={busy || !current?.history?.length}
              onClick={() => void handleUndo()}
              title="Letzten Bearbeitungsschritt dieser Variante rückgängig machen"
            >
              <RotateCcw size={13} strokeWidth={1.75} />
              Rückgängig
            </button>

            {current && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void handleManualSave()}
                title="Kompiliert das LaTeX-PDF direkt mit allen aktuellen Daten neu (ohne KI-Prompt, Strg+S)"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
              >
                <RefreshCw size={13} className={busy ? 'spin-icon' : ''} strokeWidth={2} />
                <span>{busy ? 'Kompiliere …' : 'PDF neu kompilieren'}</span>
              </button>
            )}

            {current && (
              <a
                className="btn"
                href={`/api/cv/variants/${current.id}/pdf/${lang}?download=true&t=${pdfNonce}`}
                title="Kompiliertes PDF herunterladen"
              >
                <Download size={13} strokeWidth={1.75} />
                PDF herunterladen
              </a>
            )}

            {(current?.job_id || selectedJobId) && (
              <button
                type="button"
                className="btn"
                onClick={async () => {
                  const jid = current?.job_id ?? selectedJobId;
                  if (!jid) return;
                  if (isCvDirtyRef.current) {
                    const ok = await handleManualSave();
                    if (!ok) return;
                  }
                  try {
                    const res = await api.exportDossier(jid, lang, true, true);
                    const warnSuffix = res.warnings?.length ? ` · Hinweise: ${res.warnings.join(' | ')}` : '';
                    onNotify(`Bewerbungsordner geöffnet (${res.file_count} Dateien): ${res.folder}${warnSuffix}`);
                  } catch (err) {
                    onNotify(err instanceof Error ? err.message : 'Ordner konnte nicht geöffnet werden');
                  }
                }}
                title="Speichert Lebenslauf, Anschreiben & Zeugnisse im konfigurierten Export-Ordner und öffnet den Explorer"
              >
                <FolderOpen size={13} strokeWidth={1.75} />
                Ordner öffnen
              </button>
            )}
          </div>
        </div>

        {activeVerification && (
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
                      activeVerification.status === 'ok'
                        ? '#10b981'
                        : activeVerification.status === 'warn'
                        ? '#f59e0b'
                        : '#ef4444',
                  }}
                />
                <span>ATS- &amp; Layout-Gate ({lang.toUpperCase()})</span>
              </div>

              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {activeVerification.checks.map((chk) => (
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
                {activeVerification.keyword_coverage && (
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
                    🎯 Keyword-Match: {activeVerification.keyword_coverage.coverage_pct}%
                  </span>
                )}
              </div>
            </div>

            {activeVerification.issues.length > 0 && (
              <div style={{ color: '#d97706', fontSize: '10.5px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {activeVerification.issues.map((iss, i) => (
                  <div key={i}>⚠️ {iss.message}</div>
                ))}
              </div>
            )}

            {activeVerification.keyword_coverage?.missing && activeVerification.keyword_coverage.missing.length > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap', color: 'var(--ink-secondary)', fontSize: '10px' }}>
                <span>Noch nicht im CV:</span>
                {activeVerification.keyword_coverage.missing.slice(0, 6).map((kw, i) => (
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
          {current ? (
            Array.from({ length: Math.max(1, activePages) }).map((_, pageIdx) => (
              <img
                key={`${pageIdx}-${pdfNonce}`}
                alt={`Lebenslauf Seite ${pageIdx + 1}`}
                className="a4-sheet-img"
                src={`/api/cv/variants/${current.id}/preview/${lang}?page=${pageIdx}&v=${pdfNonce}`}
              />
            ))
          ) : (
            <div className="empty-state">Lade Lebenslauf …</div>
          )}
        </div>
      </section>

      {/* Right: Natural-Language AI Tailoring + Direct Bilingual Field Editor */}
      <section className="studio-editor-pane" aria-label="Lebenslauf Anpassung">
        <div className="studio-toolbar">
          <div style={{ display: 'flex', gap: '6px' }}>
            <button
              type="button"
              className={`seg-btn ${rightMode === 'ai' ? 'is-active' : ''}`}
              onClick={() => setRightMode('ai')}
            >
              KI-Anpassung per Textfenster
            </button>
            <button
              type="button"
              className={`seg-btn ${rightMode === 'manual' ? 'is-active' : ''}`}
              onClick={() => setRightMode('manual')}
            >
              Direkt-Editor (Alle Felder)
            </button>
          </div>

          {rightMode === 'manual' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {isSavingCvDraft ? (
                <span
                  style={{
                    fontSize: '11px',
                    color: 'var(--text-muted)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  <RefreshCw size={11} className="spin-icon" /> Speichert CV …
                </span>
              ) : cvDraftSavedAt ? (
                <span
                  style={{
                    fontSize: '11px',
                    color: 'var(--green, #10b981)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                  title={`CV-Daten automatisch um ${cvDraftSavedAt} Uhr in SQLite gesichert`}
                >
                  <CheckCircle2 size={11} /> Entwurf gesichert ({cvDraftSavedAt})
                </span>
              ) : null}
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void handleManualSave()}
                title="Speichert alle Eingaben und kompiliert das LaTeX-PDF direkt neu (Strg+S)"
              >
                <RefreshCw size={13} className={busy ? 'spin-icon' : ''} strokeWidth={2} />
                <span>{busy ? 'Kompiliere …' : 'PDF neu kompilieren & speichern'}</span>
              </button>
            </div>
          )}
        </div>

        {rightMode === 'ai' ? (
          <div className="studio-scroll">
            {linkedJob && (
              <div style={{ marginBottom: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', padding: '7px 12px', background: 'var(--bg-subtle-tint)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    Ziel-Stelle: <strong style={{ color: 'var(--text-primary)' }}>{linkedJob.company}</strong> — {linkedJob.title}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                    <button
                      type="button"
                      className="btn-xs"
                      onClick={() => setShowJobCheatSheet((v) => !v)}
                      title="Erkannte Anforderungen, Stärken und fehlende Keywords dieser Stelle anzeigen"
                    >
                      <span>Spickzettel</span>
                      {showJobCheatSheet ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                    </button>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11.5px', cursor: 'pointer', margin: 0 }}>
                      <input
                        type="checkbox"
                        checked={useJobContext}
                        onChange={(e) => setUseJobContext(e.target.checked)}
                      />
                      <span>Stellen-Kontext einbeziehen</span>
                    </label>
                  </div>
                </div>

                {showJobCheatSheet && (
                  <div className="cheatsheet-panel">
                    {linkedJob.ai_parsed?.evidence && linkedJob.ai_parsed.evidence.length > 0 && (
                      <div>
                        <strong>✓ Stärken hervorheben:</strong>
                        <ul>
                          {linkedJob.ai_parsed.evidence.slice(0, 4).map((ev, i) => (
                            <li key={`cv-ev-${i}`}>{ev}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {linkedJob.ai_parsed?.gaps && linkedJob.ai_parsed.gaps.length > 0 && (
                      <div>
                        <strong>⚠️ Lücken / Anforderungen:</strong>
                        <ul>
                          {linkedJob.ai_parsed.gaps.slice(0, 4).map((gp, i) => (
                            <li key={`cv-gp-${i}`}>{gp}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {activeVerification?.keyword_coverage?.missing && activeVerification.keyword_coverage.missing.length > 0 && (
                      <div>
                        <strong>🎯 Fehlende Keywords im CV:</strong>{' '}
                        {activeVerification.keyword_coverage.missing.slice(0, 8).join(', ')}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="editor-card">
              <div className="editor-card-header">
                <div className="editor-card-title-group">
                  <Sparkles size={16} className="editor-card-icon" />
                  <div>
                    <div className="editor-card-title">Lebenslauf per Anweisung anpassen</div>
                    <div className="editor-card-subtitle">DE &amp; EN werden synchron im LaTeX-Standardlayout aktualisiert</div>
                  </div>
                </div>
              </div>

              <div className="editor-card-body">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                  <div className="model-switch-group" role="radiogroup" aria-label="KI-Modell wählen">
                    <button
                      type="button"
                      className={`model-switch-btn ${aiModel === 'hydrogen' ? 'is-active' : ''}`}
                      onClick={() => setAiModel('hydrogen')}
                    >
                      <Cpu size={12} strokeWidth={2} />
                      <span>Gemini 4 Hydrogen</span>
                    </button>
                    <button
                      type="button"
                      className={`model-switch-btn is-argon ${aiModel === 'argon' ? 'is-active' : ''}`}
                      onClick={() => setAiModel('argon')}
                      title="Gemini 4 Argon — Höchste sprachliche Präzision & Argumentationstiefe"
                    >
                      <Sparkles size={12} strokeWidth={2} />
                      <span>Argon</span>
                    </button>
                    <button
                      type="button"
                      className={`model-switch-btn is-neon ${aiModel === 'neon' ? 'is-active' : ''}`}
                      onClick={() => setAiModel('neon')}
                      title="Gemini 4 Neon — Neueste Reasoning- & Strukturierungsqualität"
                    >
                      <Sparkles size={12} strokeWidth={2} />
                      <span>Neon</span>
                    </button>
                  </div>
                </div>

                <div className="field-group">
                  <span className="field-label">Schnell-Vorlagen (Klick übernimmt in Textfeld)</span>
                  <div className="prompt-chips">
                    {CV_AI_PRESETS.map((preset, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="chip-btn"
                        title={preset.text}
                        onClick={() => setPrompt(preset.text)}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="field-group">
                  <label className="field-label" htmlFor="cv-adjust-textarea">
                    Welche Anpassungen möchtest du am Lebenslauf vornehmen?
                  </label>
                  <textarea
                    id="cv-adjust-textarea"
                    className="field-textarea"
                    rows={6}
                    placeholder="Beispiel: Hebe meine Masterarbeit zu agentischen LLMs hervor, ändere die Akzentfarbe auf Dunkelblau (#1e3a8a), setze die Ausbildung vor die Berufserfahrung und optimiere die Ränder, damit alles auf exakt 1 Seite passt …"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                        e.preventDefault();
                        void handleAiAdjust();
                      }
                    }}
                  />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                  <span className="mono" style={{ fontSize: '0.72rem', color: 'var(--ink-muted)' }}>
                    Strg + Enter (KI) · Strg + S (Kompilieren)
                  </span>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button
                      type="button"
                      className="btn"
                      disabled={busy || !current}
                      onClick={() => void handleManualSave()}
                      title="Kompiliert das LaTeX-PDF direkt mit dem aktuellen Datenstand neu – ohne Gemini/KI-Anfrage (Strg+S)"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                    >
                      <RefreshCw size={13} className={busy ? 'spin-icon' : ''} strokeWidth={1.75} />
                      <span>Ohne KI neu kompilieren</span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary"
                      style={{ padding: '9px 18px' }}
                      disabled={busy || !prompt.trim()}
                      onClick={() => void handleAiAdjust()}
                    >
                      {busy ? <RefreshCw size={14} strokeWidth={1.75} /> : <Cpu size={14} strokeWidth={1.75} />}
                      {busy ? 'Passe an & kompiliere PDF …' : 'Anpassung anwenden & PDF aktualisieren'}
                    </button>
                  </div>
                </div>

                {lastSummary && (
                  <div className="ai-panel">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                      <CheckCircle2 size={14} strokeWidth={2} color="var(--badge-fresh-fg)" />
                      <strong style={{ fontSize: 'var(--text-xs)' }}>Durchgeführte Anpassung</strong>
                    </div>
                    <p style={{ fontSize: 'var(--text-xs)', color: 'var(--ink-secondary)' }}>{lastSummary}</p>
                  </div>
                )}
              </div>
            </div>

            {current?.history && current.history.length > 0 && (
              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <RotateCcw size={15} className="editor-card-icon" />
                    <div>
                      <div className="editor-card-title">Versionsverlauf dieser Variante</div>
                      <div className="editor-card-subtitle">{current.history.length} Versionen rückgängig machbar</div>
                    </div>
                  </div>
                </div>
                <div className="editor-card-body" style={{ gap: '6px' }}>
                  {[...current.history].reverse().slice(0, 6).map((h, i) => (
                    <div
                      key={i}
                      style={{
                        padding: '7px 11px',
                        background: 'var(--bg-sheet)',
                        border: '1px solid var(--line-hairline)',
                        borderRadius: '6px',
                        fontSize: 'var(--text-xs)',
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: '10px',
                      }}
                    >
                      <span>{h.label}</span>
                      <span className="mono" style={{ color: 'var(--ink-muted)' }}>
                        {h.ts.slice(11, 16)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          /* Direct Structured Field Editor */
          cvDraft && (
            <div className="studio-scroll">
              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Rollen-Titel &amp; Kurzprofil ({lang.toUpperCase()})</span>
                  </div>
                </div>
                <div className="editor-card-body">
                  <div className="form-grid-2">
                    <div className="field-group">
                      <label className="field-label">Rollen-Titel Zeile 1</label>
                      <input
                        className="field-input"
                        value={cvDraft.role_line1[lang]}
                        onChange={(e) =>
                          handleUpdateCvDraft({
                            ...cvDraft,
                            role_line1: { ...cvDraft.role_line1, [lang]: e.target.value },
                          })
                        }
                      />
                    </div>
                    <div className="field-group">
                      <label className="field-label">Rollen-Titel Zeile 2</label>
                      <input
                        className="field-input"
                        value={cvDraft.role_line2[lang]}
                        onChange={(e) =>
                          handleUpdateCvDraft({
                            ...cvDraft,
                            role_line2: { ...cvDraft.role_line2, [lang]: e.target.value },
                          })
                        }
                      />
                    </div>
                  </div>

                  <div className="field-group">
                    <label className="field-label">Kurzprofil</label>
                    <textarea
                      className="field-textarea"
                      rows={4}
                      value={cvDraft.profile[lang]}
                      onChange={(e) =>
                        handleUpdateCvDraft({
                          ...cvDraft,
                          profile: { ...cvDraft.profile, [lang]: e.target.value },
                        })
                      }
                    />
                  </div>
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <ImageIcon size={15} strokeWidth={1.75} />
                    <span className="editor-card-title">🎨 Design, Layout, Farben &amp; Porträt</span>
                  </div>
                </div>
                <div className="editor-card-body">
                  {/* Colors & Typography Row */}
                  <div className="form-grid-2" style={{ marginBottom: '14px' }}>
                    <div className="field-group">
                      <label className="field-label">Akzentfarbe (Kopfzeile, Linien &amp; Datums-Badges)</label>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <input
                          type="color"
                          value={cvDraft.accent_color || '#3b4d61'}
                          onChange={(e) =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              accent_color: e.target.value,
                            })
                          }
                          style={{ width: '38px', height: '32px', padding: '2px', border: '1px solid var(--border)', borderRadius: '4px', cursor: 'pointer' }}
                        />
                        <input
                          className="field-input"
                          style={{ flex: 1, fontFamily: 'monospace' }}
                          value={cvDraft.accent_color || '#3b4d61'}
                          onChange={(e) =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              accent_color: e.target.value,
                            })
                          }
                          placeholder="#3b4d61"
                        />
                      </div>
                      <div style={{ display: 'flex', gap: '4px', marginTop: '6px', flexWrap: 'wrap' }}>
                        {[
                          { label: 'Schiefer', hex: '#3b4d61' },
                          { label: 'Navy', hex: '#1e3a8a' },
                          { label: 'Teal', hex: '#0f766e' },
                          { label: 'Anthrazit', hex: '#334155' },
                          { label: 'Waldgrün', hex: '#15803d' },
                          { label: 'Bordeaux', hex: '#881337' },
                        ].map((c) => (
                          <button
                            key={c.hex}
                            type="button"
                            className="chip-btn"
                            style={{
                              fontSize: '10.5px',
                              padding: '2px 7px',
                              borderLeft: `8px solid ${c.hex}`,
                            }}
                            onClick={() =>
                              handleUpdateCvDraft({
                                ...cvDraft,
                                accent_color: c.hex,
                              })
                            }
                          >
                            {c.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="field-group">
                      <label className="field-label">Spaltenbreite &amp; Schriftgröße</label>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                        <div>
                          <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>Linke Spalte</span>
                          <select
                            className="field-input"
                            value={String(cvDraft.column_ratio ?? 0.31)}
                            onChange={(e) =>
                              handleUpdateCvDraft({
                                ...cvDraft,
                                column_ratio: parseFloat(e.target.value),
                              })
                            }
                          >
                            <option value="0.27">Kompakt (27%)</option>
                            <option value="0.29">Schmal (29%)</option>
                            <option value="0.31">Standard (31%)</option>
                            <option value="0.33">Mittel (33%)</option>
                            <option value="0.35">Breit (35%)</option>
                          </select>
                        </div>
                        <div>
                          <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>Schriftgröße</span>
                          <select
                            className="field-input"
                            value={cvDraft.font_size || '10pt'}
                            onChange={(e) =>
                              handleUpdateCvDraft({
                                ...cvDraft,
                                font_size: e.target.value,
                              })
                            }
                          >
                            <option value="9pt">Kompakt (9pt)</option>
                            <option value="9.5pt">Kompakt+ (9.5pt)</option>
                            <option value="10pt">Standard (10pt)</option>
                            <option value="10.5pt">Groß (10.5pt)</option>
                          </select>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Element Spacing & Density */}
                  <div className="form-grid-2" style={{ marginBottom: '14px' }}>
                    <div className="field-group">
                      <label className="field-label">Layout-Dichte &amp; Abstände</label>
                      <select
                        className="field-input"
                        value={cvDraft.density || 'normal'}
                        onChange={(e) => {
                          const d = e.target.value as 'compact' | 'normal' | 'relaxed' | 'generous';
                          if (d === 'relaxed') {
                            handleUpdateCvDraft({
                              ...cvDraft,
                              density: 'relaxed',
                              left_section_spacing: '24pt',
                              skill_item_spacing: '9pt',
                              skill_lang_spacing: '24pt',
                              interest_item_spacing: '8pt',
                              contact_item_spacing: '7pt',
                              main_section_spacing: '8pt',
                              event_spacing: '7.5pt',
                              item_spacing: '3.2pt',
                              margins: { top: '0.6cm', bottom: '0.5cm', left: '0.9cm', right: '0.9cm' },
                            });
                          } else if (d === 'generous') {
                            handleUpdateCvDraft({
                              ...cvDraft,
                              density: 'generous',
                              left_section_spacing: '28pt',
                              skill_item_spacing: '10pt',
                              skill_lang_spacing: '28pt',
                              interest_item_spacing: '9pt',
                              contact_item_spacing: '8pt',
                              main_section_spacing: '10pt',
                              event_spacing: '8.5pt',
                              item_spacing: '3.5pt',
                              margins: { top: '0.55cm', bottom: '0.45cm', left: '0.85cm', right: '0.85cm' },
                            });
                          } else if (d === 'compact') {
                            handleUpdateCvDraft({
                              ...cvDraft,
                              density: 'compact',
                              left_section_spacing: '12pt',
                              skill_item_spacing: '6pt',
                              skill_lang_spacing: '14pt',
                              interest_item_spacing: '4pt',
                              contact_item_spacing: '4pt',
                              main_section_spacing: '5pt',
                              event_spacing: '5pt',
                              item_spacing: '2.8pt',
                              margins: { top: '0.7cm', bottom: '0.6cm', left: '1.0cm', right: '1.0cm' },
                            });
                          } else {
                            handleUpdateCvDraft({
                              ...cvDraft,
                              density: 'normal',
                              left_section_spacing: '18pt',
                              skill_item_spacing: '7.5pt',
                              skill_lang_spacing: '18pt',
                              interest_item_spacing: '6pt',
                              contact_item_spacing: '5pt',
                              main_section_spacing: '6pt',
                              event_spacing: '6pt',
                              item_spacing: '3pt',
                              margins: { top: '0.7cm', bottom: '0.6cm', left: '1.0cm', right: '1.0cm' },
                            });
                          }
                        }}
                      >
                        <option value="normal">Standard (Ausgewogene Abstände)</option>
                        <option value="relaxed">Luftig (Mehr Abstand links &amp; Hauptspalte)</option>
                        <option value="generous">Sehr weit (Großzügigste Abstände)</option>
                        <option value="compact">Kompakt (Enger für mehr Text)</option>
                      </select>
                      <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
                        <button
                          type="button"
                          className="chip-btn"
                          style={{ fontSize: '10.5px', padding: '2px 7px' }}
                          onClick={() =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              density: 'relaxed',
                              left_section_spacing: '24pt',
                              skill_item_spacing: '9pt',
                              skill_lang_spacing: '24pt',
                              interest_item_spacing: '8pt',
                              contact_item_spacing: '7pt',
                              main_section_spacing: '8pt',
                              event_spacing: '7.5pt',
                              item_spacing: '3.2pt',
                              margins: { top: '0.6cm', bottom: '0.5cm', left: '0.9cm', right: '0.9cm' },
                            })
                          }
                        >
                          🌬️ Mehr Abstand (Luftig)
                        </button>
                      </div>
                    </div>

                    <div className="field-group">
                      <label className="field-label">Abstand Skills ↔ Sprachen (z.B. LLM APIs / German)</label>
                      <select
                        className="field-input"
                        value={cvDraft.skill_lang_spacing || '18pt'}
                        onChange={(e) =>
                          handleUpdateCvDraft({
                            ...cvDraft,
                            skill_lang_spacing: e.target.value,
                          })
                        }
                      >
                        <option value="14pt">Kompakt (14pt)</option>
                        <option value="18pt">Standard (18pt)</option>
                        <option value="24pt">Deutlich getrennt (24pt)</option>
                        <option value="30pt">Sehr weit (30pt)</option>
                      </select>
                    </div>
                  </div>

                  {/* Section Order & Visibility */}
                  <div className="form-grid-2" style={{ marginBottom: '14px' }}>
                    <div className="field-group">
                      <label className="field-label">Reihenfolge Hauptspalte</label>
                      <select
                        className="field-input"
                        value={
                          Array.isArray(cvDraft.section_order_right) && cvDraft.section_order_right[1] === 'education'
                            ? 'edu_first'
                            : 'exp_first'
                        }
                        onChange={(e) => {
                          const order =
                            e.target.value === 'edu_first'
                              ? ['profile', 'education', 'experience', 'signature']
                              : ['profile', 'experience', 'education', 'signature'];
                          handleUpdateCvDraft({
                            ...cvDraft,
                            section_order_right: order,
                          });
                        }}
                      >
                        <option value="exp_first">Standard (Profil → Berufserfahrung → Ausbildung)</option>
                        <option value="edu_first">Ausbildung zuerst (Profil → Ausbildung → Berufserfahrung)</option>
                      </select>
                    </div>

                    <div className="field-group">
                      <label className="field-label">Sektionen anzeigen / verbergen</label>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', paddingTop: '4px' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--text-xs)', cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={cvDraft.show_profile ?? true}
                            onChange={(e) =>
                              handleUpdateCvDraft({
                                ...cvDraft,
                                show_profile: e.target.checked,
                              })
                            }
                          />
                          <span>Profil anzeigen</span>
                        </label>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: 'var(--text-xs)', cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={cvDraft.show_interests ?? true}
                            onChange={(e) =>
                              handleUpdateCvDraft({
                                ...cvDraft,
                                show_interests: e.target.checked,
                              })
                            }
                          />
                          <span>Interessen anzeigen</span>
                        </label>
                      </div>
                    </div>
                  </div>

                  {/* Portrait & Signature Row */}
                  <div className="form-grid-2">
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
                      <input
                        type="checkbox"
                        checked={cvDraft.include_portrait ?? true}
                        onChange={(e) =>
                          handleUpdateCvDraft({
                            ...cvDraft,
                            include_portrait: e.target.checked,
                          })
                        }
                      />
                      <span><strong>Porträtfoto anzeigen</strong></span>
                    </label>

                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
                      <input
                        type="checkbox"
                        checked={cvDraft.include_signature ?? true}
                        onChange={(e) =>
                          handleUpdateCvDraft({
                            ...cvDraft,
                            include_signature: e.target.checked,
                          })
                        }
                      />
                      <span><strong>Unterschrift anzeigen</strong></span>
                    </label>
                  </div>

                  {(cvDraft.include_portrait ?? true) && (
                    <div className="form-grid-2" style={{ marginTop: '12px' }}>
                      <div className="field-group">
                        <label className="field-label">Foto-Ausrichtung</label>
                        <select
                          className="field-input"
                          value={cvDraft.portrait_align || 'left'}
                          onChange={(e) =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              portrait_align: e.target.value as 'left' | 'center' | 'right',
                            })
                          }
                        >
                          <option value="left">Linksbündig (mit Name bündig)</option>
                          <option value="center">Zentriert</option>
                          <option value="right">Rechtsbündig</option>
                        </select>
                      </div>

                      <div className="field-group">
                        <label className="field-label">Foto-Breite ({Math.round(((cvDraft.portrait_width ?? 0.75) * 100))}%)</label>
                        <select
                          className="field-input"
                          value={String(cvDraft.portrait_width ?? 0.75)}
                          onChange={(e) =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              portrait_width: parseFloat(e.target.value),
                            })
                          }
                        >
                          <option value="0.68">Kompakt (68%)</option>
                          <option value="0.75">Standard (75%)</option>
                          <option value="0.82">Breit (82%)</option>
                          <option value="0.90">Groß (90%)</option>
                        </select>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Kenntnisse &amp; Tech-Stack ({lang.toUpperCase()})</span>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={() =>
                      handleUpdateCvDraft({
                        ...cvDraft,
                        skills: [
                          ...cvDraft.skills,
                          { name_de: 'Neuer Skill', name_en: 'New Skill', level_de: 'Praxis', level_en: 'Practical use' },
                        ],
                      })
                    }
                  >
                    <Plus size={12} strokeWidth={1.75} /> Skill hinzufügen
                  </button>
                </div>
                <div className="editor-card-body">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {cvDraft.skills.map((sk, idx) => (
                      <div key={idx} style={{ display: 'grid', gridTemplateColumns: '1fr 150px auto', gap: '6px' }}>
                        <input
                          className="field-input"
                          value={lang === 'en' ? sk.name_en : sk.name_de}
                          onChange={(e) => {
                            const next = [...cvDraft.skills];
                            next[idx] =
                              lang === 'en'
                                ? { ...sk, name_en: e.target.value }
                                : { ...sk, name_de: e.target.value, name_en: sk.name_en === sk.name_de ? e.target.value : sk.name_en };
                            handleUpdateCvDraft({ ...cvDraft, skills: next });
                          }}
                        />
                        <input
                          className="field-input"
                          value={lang === 'en' ? sk.level_en : sk.level_de}
                          onChange={(e) => {
                            const next = [...cvDraft.skills];
                            next[idx] = lang === 'en' ? { ...sk, level_en: e.target.value } : { ...sk, level_de: e.target.value };
                            handleUpdateCvDraft({ ...cvDraft, skills: next });
                          }}
                        />
                        <button
                          type="button"
                          className="btn"
                          aria-label="Skill löschen"
                          onClick={() =>
                            handleUpdateCvDraft({ ...cvDraft, skills: cvDraft.skills.filter((_, i) => i !== idx) })
                          }
                        >
                          <Trash2 size={13} strokeWidth={1.75} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Sprachen ({lang.toUpperCase()})</span>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={() =>
                      handleUpdateCvDraft({
                        ...cvDraft,
                        languages: [
                          ...(cvDraft.languages || []),
                          { name_de: 'Neue Sprache', name_en: 'New Language', level_de: 'Fließend', level_en: 'Fluent' },
                        ],
                      })
                    }
                  >
                    <Plus size={12} strokeWidth={1.75} /> Sprache hinzufügen
                  </button>
                </div>
                <div className="editor-card-body">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {(cvDraft.languages || []).map((lg, idx) => (
                      <div key={idx} style={{ display: 'grid', gridTemplateColumns: '1fr 150px auto', gap: '6px' }}>
                        <input
                          className="field-input"
                          value={lang === 'en' ? lg.name_en : lg.name_de}
                          onChange={(e) => {
                            const next = [...(cvDraft.languages || [])];
                            next[idx] =
                              lang === 'en'
                                ? { ...lg, name_en: e.target.value }
                                : { ...lg, name_de: e.target.value };
                            handleUpdateCvDraft({ ...cvDraft, languages: next });
                          }}
                        />
                        <input
                          className="field-input"
                          value={lang === 'en' ? lg.level_en : lg.level_de}
                          onChange={(e) => {
                            const next = [...(cvDraft.languages || [])];
                            next[idx] = lang === 'en' ? { ...lg, level_en: e.target.value } : { ...lg, level_de: e.target.value };
                            handleUpdateCvDraft({ ...cvDraft, languages: next });
                          }}
                        />
                        <button
                          type="button"
                          className="btn"
                          aria-label="Sprache löschen"
                          onClick={() =>
                            handleUpdateCvDraft({
                              ...cvDraft,
                              languages: (cvDraft.languages || []).filter((_, i) => i !== idx),
                            })
                          }
                        >
                          <Trash2 size={13} strokeWidth={1.75} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Berufserfahrung ({lang.toUpperCase()})</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      const newExp: CvExperience = {
                        timeframe: 'MM/JJJJ - MM/JJJJ',
                        company: 'Unternehmen, Ort',
                        title_de: 'Positionsbezeichnung',
                        title_en: 'Job Title',
                        bullets_de: ['Verantwortung oder Projekterfolg 1'],
                        bullets_en: ['Responsibility or project outcome 1'],
                      };
                      handleUpdateCvDraft({
                        ...cvDraft,
                        experience: [...(cvDraft.experience || []), newExp],
                      });
                    }}
                  >
                    <Plus size={12} strokeWidth={1.75} /> Station hinzufügen
                  </button>
                </div>
                <div className="editor-card-body">
                  {cvDraft.experience.map((exp, expIdx) => {
                    const bulletsKey = lang === 'en' ? 'bullets_en' : 'bullets_de';
                    const titleKey = lang === 'en' ? 'title_en' : 'title_de';
                    return (
                      <div
                        key={expIdx}
                        style={{
                          padding: '12px',
                          background: 'var(--bg-sheet)',
                          border: '1px solid var(--line-hairline)',
                          borderRadius: '6px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '8px',
                          marginBottom: '8px',
                        }}
                      >
                        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 130px auto', gap: '6px', alignItems: 'center' }}>
                          <input
                            className="field-input"
                            placeholder="Titel / Rolle"
                            value={exp[titleKey]}
                            onChange={(e) => {
                              const next = [...cvDraft.experience];
                              next[expIdx] = { ...exp, [titleKey]: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, experience: next });
                            }}
                          />
                          <input
                            className="field-input"
                            placeholder="Unternehmen"
                            value={exp.company}
                            onChange={(e) => {
                              const next = [...cvDraft.experience];
                              next[expIdx] = { ...exp, company: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, experience: next });
                            }}
                          />
                          <input
                            className="field-input mono"
                            placeholder="Zeitraum"
                            value={exp.timeframe}
                            onChange={(e) => {
                              const next = [...cvDraft.experience];
                              next[expIdx] = { ...exp, timeframe: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, experience: next });
                            }}
                          />
                          <button
                            type="button"
                            className="btn"
                            title="Station löschen"
                            aria-label="Station löschen"
                            onClick={() => {
                              const next = cvDraft.experience.filter((_, i) => i !== expIdx);
                              handleUpdateCvDraft({ ...cvDraft, experience: next });
                            }}
                          >
                            <Trash2 size={13} strokeWidth={1.75} />
                          </button>
                        </div>
                        <textarea
                          className="field-textarea"
                          rows={Math.max(2, (exp[bulletsKey] || []).length + 1)}
                          value={(exp[bulletsKey] || []).join('\n')}
                          onChange={(e) => {
                            const next = [...cvDraft.experience];
                            next[expIdx] = {
                              ...exp,
                              [bulletsKey]: e.target.value.split('\n'),
                            };
                            handleUpdateCvDraft({ ...cvDraft, experience: next });
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Ausbildung ({lang.toUpperCase()})</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      const newEdu: CvEducation = {
                        timeframe: 'MM/JJJJ - MM/JJJJ',
                        degree_de: 'Neuer Abschluss',
                        degree_en: 'New Degree',
                        institution: 'Hochschule / Universität',
                        bullets_de: [{ label: 'Abschlussnote', text: '1,x' }],
                        bullets_en: [{ label: 'Final grade', text: '1.x' }],
                      };
                      handleUpdateCvDraft({
                        ...cvDraft,
                        education: [...(cvDraft.education || []), newEdu],
                      });
                    }}
                  >
                    <Plus size={12} strokeWidth={1.75} /> Ausbildung hinzufügen
                  </button>
                </div>
                <div className="editor-card-body">
                  {cvDraft.education.map((edu, eduIdx) => {
                    const degKey = lang === 'en' ? 'degree_en' : 'degree_de';
                    const bulletsKey = lang === 'en' ? 'bullets_en' : 'bullets_de';
                    const rawBullets = Array.isArray(edu[bulletsKey]) ? edu[bulletsKey] : [];

                    return (
                      <div
                        key={eduIdx}
                        style={{
                          padding: '12px',
                          background: 'var(--bg-sheet)',
                          border: '1px solid var(--line-hairline)',
                          borderRadius: '6px',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '10px',
                          marginBottom: '12px',
                        }}
                      >
                        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 130px auto', gap: '6px', alignItems: 'center' }}>
                          <input
                            className="field-input"
                            placeholder="Abschluss (z. B. M.Eng. Automated Driving)"
                            value={edu[degKey] || ''}
                            onChange={(e) => {
                              const next = [...cvDraft.education];
                              next[eduIdx] = { ...edu, [degKey]: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, education: next });
                            }}
                          />
                          <input
                            className="field-input"
                            placeholder="Hochschule / Institut"
                            value={edu.institution || ''}
                            onChange={(e) => {
                              const next = [...cvDraft.education];
                              next[eduIdx] = { ...edu, institution: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, education: next });
                            }}
                          />
                          <input
                            className="field-input mono"
                            placeholder="Zeitraum"
                            value={edu.timeframe || ''}
                            onChange={(e) => {
                              const next = [...cvDraft.education];
                              next[eduIdx] = { ...edu, timeframe: e.target.value };
                              handleUpdateCvDraft({ ...cvDraft, education: next });
                            }}
                          />
                          <button
                            type="button"
                            className="btn"
                            title="Diesen Ausbildungsabschnitt löschen"
                            aria-label="Ausbildung löschen"
                            onClick={() => {
                              const next = cvDraft.education.filter((_, i) => i !== eduIdx);
                              handleUpdateCvDraft({ ...cvDraft, education: next });
                            }}
                          >
                            <Trash2 size={13} strokeWidth={1.75} />
                          </button>
                        </div>

                        {/* Bullets / Details for Education */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', paddingLeft: '8px', borderLeft: '2px solid var(--accent-subtle)' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2px' }}>
                            <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>
                              Stichpunkte / Noten / Projekte ({lang.toUpperCase()}):
                            </span>
                            <button
                              type="button"
                              className="btn btn-xs"
                              style={{ fontSize: '0.72rem', padding: '2px 8px' }}
                              onClick={() => {
                                const cur = [...rawBullets];
                                cur.push({ label: lang === 'en' ? 'Project' : 'Studienprojekt', text: '' });
                                const next = [...cvDraft.education];
                                next[eduIdx] = { ...edu, [bulletsKey]: cur };
                                handleUpdateCvDraft({ ...cvDraft, education: next });
                              }}
                            >
                              <Plus size={11} strokeWidth={1.75} /> Stichpunkt hinzufügen
                            </button>
                          </div>

                          {rawBullets.map((b: any, bIdx: number) => {
                            const isObj = typeof b === 'object' && b !== null;
                            const bLabel = isObj ? (b.label || '') : '';
                            const bText = isObj ? (b.text || '') : String(b || '');

                            return (
                              <div key={bIdx} style={{ display: 'grid', gridTemplateColumns: '160px 1fr auto', gap: '6px', alignItems: 'center' }}>
                                <input
                                  className="field-input"
                                  style={{ fontSize: '0.8rem' }}
                                  placeholder="Label (z. B. Abschlussnote, Bachelorarbeit)"
                                  value={bLabel}
                                  onChange={(e) => {
                                    const cur = [...rawBullets];
                                    cur[bIdx] = isObj ? { ...b, label: e.target.value } : { label: e.target.value, text: bText };
                                    const next = [...cvDraft.education];
                                    next[eduIdx] = { ...edu, [bulletsKey]: cur };
                                    handleUpdateCvDraft({ ...cvDraft, education: next });
                                  }}
                                />
                                <input
                                  className="field-input"
                                  style={{ fontSize: '0.8rem' }}
                                  placeholder="Beschreibung / Details"
                                  value={bText}
                                  onChange={(e) => {
                                    const cur = [...rawBullets];
                                    cur[bIdx] = isObj ? { ...b, text: e.target.value } : { label: bLabel, text: e.target.value };
                                    const next = [...cvDraft.education];
                                    next[eduIdx] = { ...edu, [bulletsKey]: cur };
                                    handleUpdateCvDraft({ ...cvDraft, education: next });
                                  }}
                                />
                                <button
                                  type="button"
                                  className="btn"
                                  style={{ padding: '4px' }}
                                  title="Stichpunkt löschen"
                                  aria-label="Stichpunkt löschen"
                                  onClick={() => {
                                    const cur = rawBullets.filter((_, i) => i !== bIdx);
                                    const next = [...cvDraft.education];
                                    next[eduIdx] = { ...edu, [bulletsKey]: cur };
                                    handleUpdateCvDraft({ ...cvDraft, education: next });
                                  }}
                                >
                                  <Trash2 size={12} strokeWidth={1.75} />
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Interessen ({lang.toUpperCase()})</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      const newInterest = { de: 'Neues Interesse', en: 'New interest' };
                      handleUpdateCvDraft({
                        ...cvDraft,
                        interests: [...(cvDraft.interests || []), newInterest],
                      });
                    }}
                  >
                    <Plus size={12} strokeWidth={1.75} /> Interesse hinzufügen
                  </button>
                </div>
                <div className="editor-card-body">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {(cvDraft.interests || []).map((it, idx) => (
                      <div key={idx} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '6px' }}>
                        <input
                          className="field-input"
                          value={typeof it === 'object' && it !== null ? (lang === 'en' ? it.en : it.de) : String(it)}
                          onChange={(e) => {
                            const next = [...cvDraft.interests];
                            if (typeof it === 'object' && it !== null) {
                              next[idx] = lang === 'en' ? { ...it, en: e.target.value } : { ...it, de: e.target.value };
                            } else {
                              next[idx] = { de: e.target.value, en: e.target.value };
                            }
                            handleUpdateCvDraft({ ...cvDraft, interests: next });
                          }}
                        />
                        <button
                          type="button"
                          className="btn"
                          aria-label="Interesse löschen"
                          onClick={() =>
                            handleUpdateCvDraft({ ...cvDraft, interests: cvDraft.interests.filter((_, i) => i !== idx) })
                          }
                        >
                          <Trash2 size={13} strokeWidth={1.75} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="editor-card">
                <div className="editor-card-header">
                  <div className="editor-card-title-group">
                    <span className="editor-card-title">Ort &amp; Unterschriftszeile</span>
                  </div>
                </div>
                <div className="editor-card-body">
                  <div className="field-group">
                    <label className="field-label">Ort der Datumszeile (z. B. München)</label>
                    <input
                      className="field-input"
                      placeholder="München"
                      value={cvDraft.signature_city || ''}
                      onChange={(e) =>
                        handleUpdateCvDraft({
                          ...cvDraft,
                          signature_city: e.target.value,
                        })
                      }
                    />
                  </div>
                </div>
              </div>

              <div
                style={{
                  position: 'sticky',
                  bottom: 0,
                  background: 'var(--bg-panel)',
                  padding: '12px 16px',
                  borderTop: '1px solid var(--border)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  boxShadow: '0 -4px 12px rgba(0,0,0,0.06)',
                  zIndex: 10,
                  marginTop: '16px',
                  borderRadius: '6px',
                }}
              >
                <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                  {cvDraftSavedAt ? `Entwurf gesichert (${cvDraftSavedAt})` : 'Tastenkürzel: Strg+S'}
                </span>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => void handleManualSave()}
                  title="PDF jetzt neu kompilieren (ohne KI, Strg+S)"
                >
                  <RefreshCw size={13} className={busy ? 'spin-icon' : ''} strokeWidth={2} />
                  <span>{busy ? 'Kompiliere …' : 'PDF neu kompilieren & speichern'}</span>
                </button>
              </div>
            </div>
          )
        )}
      </section>
    </div>
    </div>
  );
};
