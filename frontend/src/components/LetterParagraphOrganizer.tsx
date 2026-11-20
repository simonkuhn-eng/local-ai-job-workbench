import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  BookmarkPlus,
  CheckCircle2,
  Cpu,
  GripVertical,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import type {
  JobDetail,
  LetterParagraphItem,
  ParagraphRecommendation,
} from '../types';

interface LetterParagraphOrganizerProps {
  lang: 'de' | 'en';
  targetCompany?: string;
  bodyText: string;
  onChangeBodyText: (nextBodyText: string) => void;
  paragraphs: LetterParagraphItem[];
  loadingParagraphs: boolean;
  linkedJob: JobDetail | null;
  recommendations: Record<number, ParagraphRecommendation>;
  recSummary: string;
  recBusy: boolean;
  adaptBusy?: boolean;
  onRunHydrogenMatch: () => Promise<void> | void;
  onAdaptBlocksWithHydrogen?: () => Promise<void> | void;
  onSaveParagraphToLibrary: (text: string, idx: number, total: number) => Promise<void> | void;
  onDeleteParagraphFromLibrary: (paragraphId: number) => Promise<void> | void;
  onNotify: (msg: string) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  intro: 'Einleitung',
  experience: 'Berufserfahrung',
  projects: 'Projekte',
  body: 'Fokus / Tech',
  closing: 'Abschluss',
};

type DragPayload =
  | { kind: 'library'; paragraphId: number; text: string; sourceCompany?: string }
  | { kind: 'active'; index: number; text: string };

const PROTECTED_EMPLOYERS = new Set([
  'example robotics',
  'example robotics gmbh',
  'sample ai systems',
  'sample ai systems gmbh',
  'technische universität musterstadt',
]);

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function quickAdaptLibraryBlock(
  text: string,
  sourceCompany: string,
  targetCompany: string,
  draftLang: 'de' | 'en'
): { text: string; notes: string[] } {
  let out = text;
  const notes: string[] = [];
  const srcClean = (sourceCompany || '').trim();
  const tgtClean = (targetCompany || '').trim();
  const srcLow = srcClean.toLowerCase();

  if (srcClean && tgtClean && srcLow !== tgtClean.toLowerCase()) {
    // Only auto-replace non-employer target company names (never corrupt past employer work history)
    if (!PROTECTED_EMPLOYERS.has(srcLow) && srcClean.length >= 3) {
      const re = new RegExp(`(?<!\\w)${escapeRegExp(srcClean)}(?!\\w)`, 'gi');
      if (re.test(out)) {
        out = out.replace(re, tgtClean);
        notes.push(`${srcClean} → ${tgtClean}`);
      }
    }
  }

  if (draftLang === 'en') {
    const enDateRe = /\b(?:available\s+)?(?:starting|from)\s+(?:mid[- ]?)?november\s+2026\b/gi;
    if (enDateRe.test(out)) {
      out = out.replace(enDateRe, 'available immediately');
      notes.push('Eintrittsdatum → immediately');
    }
  } else {
    const deDateRe = /\bab\s+(?:Mitte|Ende|Anfang)?\s*(?:Oktober|November|Dezember)\s+2026\b/gi;
    if (deDateRe.test(out)) {
      out = out.replace(deDateRe, 'ab sofort');
      notes.push('Eintrittsdatum → ab sofort');
    }
  }

  return { text: out, notes };
}

export function splitBodyParagraphs(bodyText: string): string[] {
  const normalized = (bodyText || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];
  return normalized
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

export const LetterParagraphOrganizer: React.FC<LetterParagraphOrganizerProps> = ({
  lang,
  targetCompany,
  bodyText,
  onChangeBodyText,
  paragraphs,
  loadingParagraphs,
  linkedJob,
  recommendations,
  recSummary,
  recBusy,
  adaptBusy,
  onRunHydrogenMatch,
  onAdaptBlocksWithHydrogen,
  onSaveParagraphToLibrary,
  onDeleteParagraphFromLibrary,
  onNotify,
}) => {
  const [libSearch, setLibSearch] = useState('');
  const [libLangFilter, setLibLangFilter] = useState<'all' | 'de' | 'en'>(lang);
  const [libCatFilter, setLibCatFilter] = useState<string>('all');

  useEffect(() => {
    setLibLangFilter(lang);
  }, [lang]);

  const effectiveTargetCompany = (targetCompany || linkedJob?.company || '').trim();

  const [draggingPayload, setDraggingPayload] = useState<DragPayload | null>(null);
  const [hoverInsertIdx, setHoverInsertIdx] = useState<number | null>(null);
  const [hoverReplaceIdx, setHoverReplaceIdx] = useState<number | null>(null);
  const [undoBodySnapshot, setUndoBodySnapshot] = useState<string | null>(null);

  const activeParagraphs = useMemo(() => splitBodyParagraphs(bodyText), [bodyText]);

  const savedHashSet = useMemo(() => {
    const s = new Set<string>();
    for (const p of paragraphs) {
      s.add(p.text.replace(/\s+/g, ' ').trim().toLowerCase());
    }
    return s;
  }, [paragraphs]);

  const applyParagraphs = (nextParas: string[], recordUndo = true) => {
    if (recordUndo) {
      setUndoBodySnapshot(bodyText);
    }
    const cleaned = nextParas.map((p) => p.trim()).filter((p) => p.length > 0);
    onChangeBodyText(cleaned.join('\n\n'));
  };

  const handleUndo = () => {
    if (undoBodySnapshot === null) return;
    const prev = undoBodySnapshot;
    setUndoBodySnapshot(null);
    onChangeBodyText(prev);
    onNotify('Letzte Absatz-Änderung rückgängig gemacht.');
  };

  const handleInsertAt = (insertIdx: number, payload: DragPayload) => {
    const next = [...activeParagraphs];
    if (payload.kind === 'library') {
      const adapted = quickAdaptLibraryBlock(
        payload.text,
        payload.sourceCompany || '',
        effectiveTargetCompany,
        lang
      );
      next.splice(insertIdx, 0, adapted.text);
      applyParagraphs(next);
      onNotify(
        adapted.notes.length > 0
          ? `Absatz an Position #${insertIdx + 1} eingefügt (${adapted.notes.join(', ')}).`
          : `Absatz an Position #${insertIdx + 1} eingefügt.`
      );
      return;
    }
    // Reorder active paragraph inside draft
    const fromIdx = payload.index;
    if (fromIdx < 0 || fromIdx >= next.length) return;
    const [moved] = next.splice(fromIdx, 1);
    const targetIdx = insertIdx > fromIdx ? insertIdx - 1 : insertIdx;
    next.splice(Math.max(0, Math.min(next.length, targetIdx)), 0, moved);
    applyParagraphs(next);
  };

  const handleReplaceAt = (targetIdx: number, payload: DragPayload) => {
    const next = [...activeParagraphs];
    if (targetIdx < 0 || targetIdx >= next.length) return;
    if (payload.kind === 'library') {
      const adapted = quickAdaptLibraryBlock(
        payload.text,
        payload.sourceCompany || '',
        effectiveTargetCompany,
        lang
      );
      next[targetIdx] = adapted.text;
      applyParagraphs(next);
      onNotify(
        adapted.notes.length > 0
          ? `Absatz #${targetIdx + 1} ausgetauscht (${adapted.notes.join(', ')}).`
          : `Absatz #${targetIdx + 1} ausgetauscht.`
      );
      return;
    }
    // Swap two active paragraphs in draft
    const fromIdx = payload.index;
    if (fromIdx === targetIdx || fromIdx < 0 || fromIdx >= next.length) return;
    const tmp = next[targetIdx];
    next[targetIdx] = next[fromIdx];
    next[fromIdx] = tmp;
    applyParagraphs(next);
  };

  const handleUpdateActiveText = (idx: number, val: string) => {
    const next = [...activeParagraphs];
    next[idx] = val;
    onChangeBodyText(next.join('\n\n'));
  };

  const handleRemoveActive = (idx: number) => {
    const next = [...activeParagraphs];
    next.splice(idx, 1);
    applyParagraphs(next);
  };

  const handleMoveActive = (idx: number, delta: -1 | 1) => {
    const target = idx + delta;
    if (target < 0 || target >= activeParagraphs.length) return;
    const next = [...activeParagraphs];
    const tmp = next[target];
    next[target] = next[idx];
    next[idx] = tmp;
    applyParagraphs(next);
  };

  const handleAddEmptyParagraph = () => {
    const next = [...activeParagraphs, 'Neuer Absatz …'];
    setUndoBodySnapshot(bodyText);
    onChangeBodyText(next.join('\n\n'));
  };

  const filteredLibrary = useMemo(() => {
    const q = libSearch.trim().toLowerCase();
    const filtered = paragraphs.filter((p) => {
      if (libLangFilter !== 'all' && p.lang !== libLangFilter) return false;
      if (libCatFilter !== 'all' && p.category !== libCatFilter) return false;
      if (!q) return true;
      return (
        p.text.toLowerCase().includes(q) ||
        p.label.toLowerCase().includes(q) ||
        p.company.toLowerCase().includes(q) ||
        p.job_title.toLowerCase().includes(q)
      );
    });

    return [...filtered].sort((a, b) => {
      const ra = recommendations[a.id];
      const rb = recommendations[b.id];
      const recA = ra?.recommended ? 1 : 0;
      const recB = rb?.recommended ? 1 : 0;
      if (recA !== recB) return recB - recA;
      if (ra && rb) {
        if (recA && ra.suggested_slot !== rb.suggested_slot) {
          return ra.suggested_slot - rb.suggested_slot;
        }
        return rb.fit_score - ra.fit_score;
      }
      // Prefer paragraphs matching current draft language slightly first
      const langA = a.lang === lang ? 1 : 0;
      const langB = b.lang === lang ? 1 : 0;
      if (langA !== langB) return langB - langA;
      return 0;
    });
  }, [paragraphs, libSearch, libLangFilter, libCatFilter, recommendations, lang]);

  const recommendedCount = useMemo(
    () => Object.values(recommendations).filter((r) => r.recommended).length,
    [recommendations]
  );

  const handleAssembleRecommended = () => {
    const picked = paragraphs
      .filter((p) => recommendations[p.id]?.recommended)
      .sort(
        (a, b) =>
          (recommendations[a.id]?.suggested_slot ?? 99) -
          (recommendations[b.id]?.suggested_slot ?? 99)
      );
    if (picked.length === 0) return;
    applyParagraphs(picked.map((p) => p.text));
    onNotify(`${picked.length} empfohlene Hydrogen-Absätze ins Anschreiben übernommen.`);
  };

  const parseDragData = (e: React.DragEvent): DragPayload | null => {
    if (draggingPayload) return draggingPayload;
    for (const mime of ['application/json', 'text/plain']) {
      try {
        const raw = e.dataTransfer.getData(mime);
        if (raw) {
          const parsed = JSON.parse(raw) as DragPayload;
          if (parsed && (parsed.kind === 'library' || parsed.kind === 'active')) {
            return parsed;
          }
        }
      } catch {
        // ignore parse fallback
      }
    }
    return null;
  };

  const resetDragHover = () => {
    setDraggingPayload(null);
    setHoverInsertIdx(null);
    setHoverReplaceIdx(null);
  };

  return (
    <div className="paragraph-baukasten">
      <div className="paragraph-baukasten-grid">
        {/* Left: Current Draft Paragraph Slots */}
        <div className="baukasten-col">
          <div className="baukasten-col-head">
            <div>
              <strong>Aktuelle Absätze im Anschreiben ({activeParagraphs.length})</strong>
              <span className="baukasten-col-sub">
                Ziehe Bausteine von rechts auf einen Absatz zum Austauschen oder dazwischen zum Einfügen
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
              {undoBodySnapshot !== null && (
                <button
                  type="button"
                  className="btn-xs"
                  onClick={handleUndo}
                  title="Letzte Baukasten-Änderung rückgängig machen"
                >
                  <Undo2 size={12} />
                  <span>Rückgängig</span>
                </button>
              )}
              {onAdaptBlocksWithHydrogen && (
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ fontSize: '0.72rem', padding: '4px 9px' }}
                  disabled={Boolean(adaptBusy) || activeParagraphs.length === 0 || !linkedJob}
                  onClick={() => void onAdaptBlocksWithHydrogen()}
                  title={
                    linkedJob
                      ? `Passt Firmennamen, Übergänge und Rollenbezug behutsam mit Hydrogen für „${linkedJob.company}“ an (deine Struktur bleibt erhalten)`
                      : 'Bitte wähle zuerst eine Ziel-Stelle aus'
                  }
                >
                  {adaptBusy ? <RefreshCw size={11} className="spin-icon" /> : <Sparkles size={11} />}
                  <span>{adaptBusy ? 'Hydrogen passt an …' : 'Mit Hydrogen leicht anpassen'}</span>
                </button>
              )}
              <button
                type="button"
                className="btn-xs"
                onClick={handleAddEmptyParagraph}
                title="Leeren Absatz am Ende ergänzen"
              >
                <Plus size={12} />
                <span>Neuer Absatz</span>
              </button>
            </div>
          </div>

          <div className="baukasten-slots-scroll">
            {/* Top insert slot (index 0) */}
            <div
              className={`para-insert-slot ${draggingPayload ? 'is-drag-active' : ''} ${
                hoverInsertIdx === 0 ? 'is-hover' : ''
              }`}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
                if (hoverInsertIdx !== 0) setHoverInsertIdx(0);
              }}
              onDragLeave={(e) => {
                if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                if (hoverInsertIdx === 0) setHoverInsertIdx(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                const payload = parseDragData(e);
                resetDragHover();
                if (payload) handleInsertAt(0, payload);
              }}
            >
              <span>➕ Am Anfang (als Absatz #1) einfügen</span>
            </div>

            {activeParagraphs.length === 0 ? (
              <div
                className={`baukasten-empty-drop ${draggingPayload ? 'is-drag-active' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setHoverInsertIdx(0);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const payload = parseDragData(e);
                  resetDragHover();
                  if (payload) handleInsertAt(0, payload);
                }}
              >
                Ziehe einen bewährten Absatz aus der Auswahl rechts hierher, um dein Anschreiben
                zusammenzustellen.
              </div>
            ) : (
              activeParagraphs.map((paraText, idx) => {
                const words = paraText.trim().split(/\s+/).filter(Boolean).length;
                const isSavedInLib = savedHashSet.has(
                  paraText.replace(/\s+/g, ' ').trim().toLowerCase()
                );
                const isReplaceHover = hoverReplaceIdx === idx;

                return (
                  <React.Fragment key={idx}>
                    <div
                      className={`active-para-card ${isReplaceHover ? 'is-drop-replace' : ''}`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        if (draggingPayload?.kind === 'active' && draggingPayload.index === idx) {
                          return;
                        }
                        e.dataTransfer.dropEffect =
                          draggingPayload?.kind === 'library' ? 'copy' : 'move';
                        if (hoverReplaceIdx !== idx) setHoverReplaceIdx(idx);
                      }}
                      onDragLeave={(e) => {
                        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                        if (hoverReplaceIdx === idx) setHoverReplaceIdx(null);
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        const payload = parseDragData(e);
                        resetDragHover();
                        if (payload) handleReplaceAt(idx, payload);
                      }}
                    >
                      <div
                        className="active-para-head"
                        draggable
                        style={{ cursor: 'grab' }}
                        onDragStart={(e) => {
                          const p: DragPayload = { kind: 'active', index: idx, text: paraText };
                          const jsonStr = JSON.stringify(p);
                          setDraggingPayload(p);
                          e.dataTransfer.effectAllowed = 'move';
                          e.dataTransfer.setData('application/json', jsonStr);
                          e.dataTransfer.setData('text/plain', jsonStr);
                        }}
                        onDragEnd={resetDragHover}
                      >
                        <div className="active-para-title">
                          <GripVertical size={13} className="para-grip" />
                          <span className="para-slot-num num">Absatz #{idx + 1}</span>
                          <span className="para-words-sub num">{words} Wörter</span>
                        </div>
                        <div className="active-para-actions">
                          {!isSavedInLib && paraText.trim().length >= 35 && (
                            <button
                              type="button"
                              className="btn-xs"
                              onClick={() =>
                                void onSaveParagraphToLibrary(
                                  paraText,
                                  idx,
                                  activeParagraphs.length
                                )
                              }
                              title="Diesen Absatz in die persönliche Auswahl übernehmen"
                            >
                              <BookmarkPlus size={11} />
                              <span>In Auswahl merken</span>
                            </button>
                          )}
                          <button
                            type="button"
                            className="prio-move-btn"
                            disabled={idx === 0}
                            onClick={() => handleMoveActive(idx, -1)}
                            title="Nach oben"
                          >
                            <ArrowUp size={11} />
                          </button>
                          <button
                            type="button"
                            className="prio-move-btn"
                            disabled={idx === activeParagraphs.length - 1}
                            onClick={() => handleMoveActive(idx, 1)}
                            title="Nach unten"
                          >
                            <ArrowDown size={11} />
                          </button>
                          <button
                            type="button"
                            className="prio-move-btn is-del"
                            onClick={() => handleRemoveActive(idx)}
                            title={`Absatz #${idx + 1} aus Entwurf entfernen`}
                          >
                            <X size={11} />
                          </button>
                        </div>
                      </div>

                      {isReplaceHover && (
                        <div className="para-replace-hint">
                          {draggingPayload?.kind === 'library'
                            ? `🔄 Absatz #${idx + 1} hiermit ersetzen (alter Text fliegt raus)`
                            : `⇅ Mit Absatz #${(draggingPayload?.kind === 'active' ? draggingPayload.index + 1 : idx + 1)} tauschen`}
                        </div>
                      )}

                      <textarea
                        className="active-para-textarea"
                        rows={Math.max(3, Math.min(9, Math.ceil(paraText.length / 88) + 1))}
                        value={paraText}
                        onChange={(e) => handleUpdateActiveText(idx, e.target.value)}
                      />
                    </div>

                    {/* Insert slot after this paragraph */}
                    <div
                      className={`para-insert-slot ${draggingPayload ? 'is-drag-active' : ''} ${
                        hoverInsertIdx === idx + 1 ? 'is-hover' : ''
                      }`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect =
                          draggingPayload?.kind === 'library' ? 'copy' : 'move';
                        if (hoverInsertIdx !== idx + 1) setHoverInsertIdx(idx + 1);
                      }}
                      onDragLeave={(e) => {
                        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                        if (hoverInsertIdx === idx + 1) setHoverInsertIdx(null);
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        const payload = parseDragData(e);
                        resetDragHover();
                        if (payload) handleInsertAt(idx + 1, payload);
                      }}
                    >
                      <span>
                        ➕ Nach Absatz #{idx + 1} dazwischen einfügen
                      </span>
                    </div>
                  </React.Fragment>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Saved / Sent Paragraph Library + Hydrogen Matcher */}
        <div className="baukasten-col baukasten-lib-col">
          <div className="baukasten-col-head">
            <div>
              <strong>Bewährte Absatz-Auswahl ({paragraphs.length})</strong>
              <span className="baukasten-col-sub">
                Alle versendeten &amp; gespeicherten Absätze — zum Ziehen oder 1-Klick-Tausch
              </span>
            </div>

            <button
              type="button"
              className="btn btn-primary"
              style={{ fontSize: '0.74rem', padding: '5px 10px' }}
              disabled={recBusy || !linkedJob}
              onClick={() => void onRunHydrogenMatch()}
              title={
                linkedJob
                  ? `Prüft alle gespeicherten Absätze mit Gemini 4 Hydrogen für „${linkedJob.company} — ${linkedJob.title}“`
                  : 'Bitte zuerst eine Ziel-Stelle auswählen'
              }
            >
              {recBusy ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <Cpu size={12} strokeWidth={2} />
              )}
              <span>
                {recBusy
                  ? 'Hydrogen analysiert …'
                  : 'Mit Hydrogen passende Absätze markieren'}
              </span>
            </button>
          </div>

          {recSummary && (
            <div className="hydrogen-summary-banner">
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: '7px' }}>
                <Sparkles size={14} color="var(--accent-blue)" style={{ flexShrink: 0, marginTop: '2px' }} />
                <div>
                  <strong>Hydrogen-Auswahl{linkedJob ? ` für ${linkedJob.company}` : ''}:</strong>{' '}
                  {recSummary}
                </div>
              </div>
              {recommendedCount > 0 && (
                <button
                  type="button"
                  className="btn-xs"
                  onClick={handleAssembleRecommended}
                  title="Übernimmt alle von Hydrogen markierten Absätze in vorgeschlagener Reihenfolge ins Anschreiben"
                >
                  <Sparkles size={11} />
                  <span>Top-{recommendedCount} als Entwurf übernehmen</span>
                </button>
              )}
            </div>
          )}

          <div className="baukasten-lib-filters">
            <div className="baukasten-search-box">
              <Search size={12} className="baukasten-search-icon" />
              <input
                type="text"
                className="baukasten-search-input"
                placeholder="Suchen (z. B. RAG, ROS 2, FastAPI, Docker)…"
                value={libSearch}
                onChange={(e) => setLibSearch(e.target.value)}
              />
            </div>

            <select
              className="select-compact"
              value={libLangFilter}
              onChange={(e) => setLibLangFilter(e.target.value as 'all' | 'de' | 'en')}
              aria-label="Sprache filtern"
            >
              <option value="all">DE &amp; EN</option>
              <option value="de">Nur DE</option>
              <option value="en">Nur EN</option>
            </select>

            <select
              className="select-compact"
              value={libCatFilter}
              onChange={(e) => setLibCatFilter(e.target.value)}
              aria-label="Kategorie filtern"
            >
              <option value="all">Alle Typen</option>
              <option value="intro">Einleitung</option>
              <option value="experience">Berufserfahrung</option>
              <option value="projects">Projekte</option>
              <option value="body">Fokus / Deployment</option>
              <option value="closing">Abschluss</option>
            </select>
          </div>

          <div className="baukasten-lib-scroll">
            {loadingParagraphs && paragraphs.length === 0 ? (
              <div className="empty-state" style={{ padding: '20px' }}>
                Lade Absatz-Bibliothek …
              </div>
            ) : filteredLibrary.length === 0 ? (
              <div className="empty-state" style={{ padding: '20px' }}>
                Keine Absätze für diesen Filter gefunden.
              </div>
            ) : (
              filteredLibrary.map((p) => {
                const rec = recommendations[p.id];
                const isRec = Boolean(rec?.recommended);
                const isUsedNow = activeParagraphs.some(
                  (ap) =>
                    ap.replace(/\s+/g, ' ').trim().toLowerCase() ===
                    p.text.replace(/\s+/g, ' ').trim().toLowerCase()
                );

                return (
                  <div
                    key={p.id}
                    draggable
                    onDragStart={(e) => {
                      const payload: DragPayload = {
                        kind: 'library',
                        paragraphId: p.id,
                        text: p.text,
                        sourceCompany: p.company,
                      };
                      const jsonStr = JSON.stringify(payload);
                      setDraggingPayload(payload);
                      e.dataTransfer.effectAllowed = 'copy';
                      e.dataTransfer.setData('application/json', jsonStr);
                      e.dataTransfer.setData('text/plain', jsonStr);
                    }}
                    onDragEnd={resetDragHover}
                    className={`lib-para-card ${isRec ? 'is-recommended' : ''} ${
                      isUsedNow ? 'is-in-draft' : ''
                    }`}
                  >
                    <div className="lib-para-head">
                      <div className="lib-para-badges">
                        <GripVertical size={13} className="para-grip" />
                        <span className="comp-type-pill">
                          {p.lang.toUpperCase()}
                        </span>
                        <span className="comp-type-pill">
                          {CATEGORY_LABELS[p.category] || p.category}
                        </span>
                        {p.source_kind === 'sent' ? (
                          <span className="lib-origin-pill is-sent">
                            <CheckCircle2 size={10} />
                            <span>Versendet{p.company ? `: ${p.company}` : ''}</span>
                          </span>
                        ) : (
                          <span className="lib-origin-pill">
                            {p.company || 'Gespeichert'}
                          </span>
                        )}
                        {isUsedNow && (
                          <span className="lib-used-pill">Im aktuellen Brief</span>
                        )}
                      </div>

                      <button
                        type="button"
                        className="prio-move-btn is-del"
                        onClick={() => void onDeleteParagraphFromLibrary(p.id)}
                        title="Aus der Absatz-Auswahl entfernen"
                        aria-label="Absatz löschen"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>

                    {p.label && <div className="lib-para-label">{p.label}</div>}

                    {rec && (
                      <div className={`hydrogen-rec-badge ${isRec ? 'is-top' : 'is-normal'}`}>
                        <span className="num" style={{ fontWeight: 700 }}>
                          {isRec ? '🎯 Hydrogen Empfehlung' : 'Hydrogen Fit'}: {rec.fit_score}%
                          {isRec ? ` · Vorschlag: Absatz #${rec.suggested_slot}` : ''}
                        </span>
                        {rec.reason && <span className="hydrogen-rec-reason">{rec.reason}</span>}
                      </div>
                    )}

                    <p className="lib-para-text">{p.text}</p>

                    <div className="lib-para-actions">
                      <button
                        type="button"
                        className="btn-xs"
                        onClick={() => {
                          const adapted = quickAdaptLibraryBlock(
                            p.text,
                            p.company,
                            effectiveTargetCompany,
                            lang
                          );
                          applyParagraphs([...activeParagraphs, adapted.text]);
                          onNotify(
                            adapted.notes.length > 0
                              ? `Absatz angehängt (${adapted.notes.join(', ')}).`
                              : 'Absatz am Ende des Anschreibens hinzugefügt.'
                          );
                        }}
                      >
                        <Plus size={11} />
                        <span>Anhängen</span>
                      </button>

                      {activeParagraphs.map((_, slotIdx) => (
                        <button
                          key={slotIdx}
                          type="button"
                          className="btn-xs"
                          onClick={() => {
                            const adapted = quickAdaptLibraryBlock(
                              p.text,
                              p.company,
                              effectiveTargetCompany,
                              lang
                            );
                            const next = [...activeParagraphs];
                            next[slotIdx] = adapted.text;
                            applyParagraphs(next);
                            onNotify(
                              adapted.notes.length > 0
                                ? `Absatz #${slotIdx + 1} ersetzt (${adapted.notes.join(', ')}).`
                                : `Absatz #${slotIdx + 1} ersetzt.`
                            );
                          }}
                          title={`Ersetzt den bisherigen Absatz #${slotIdx + 1} durch diesen Baustein`}
                        >
                          ⇄ #{slotIdx + 1}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
