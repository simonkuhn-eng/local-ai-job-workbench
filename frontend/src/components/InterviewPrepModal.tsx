import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy, Cpu, RefreshCw, Sparkles, X } from 'lucide-react';
import { api } from '../api';
import type { InterviewPrepData, JobSummary } from '../types';

interface InterviewPrepModalProps {
  job: JobSummary;
  onClose: () => void;
  onNotify: (msg: string) => void;
}

export const InterviewPrepModal: React.FC<InterviewPrepModalProps> = ({ job, onClose, onNotify }) => {
  const [data, setData] = useState<InterviewPrepData | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [model, setModel] = useState<'hydrogen' | 'argon' | 'neon'>('hydrogen');
  const [lang, setLang] = useState<'de' | 'en'>('de');
  const [copiedSection, setCopiedSection] = useState<string | null>(null);
  const activeJobIdRef = useRef(job.id);
  activeJobIdRef.current = job.id;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(null);
    void (async () => {
      try {
        const res = await api.getInterviewPrep(job.id);
        if (cancelled) return;
        if (res.prep) {
          setData(res.prep);
          setModel(
            res.model && res.model.includes('neon')
              ? 'neon'
              : res.model && res.model.includes('argon')
              ? 'argon'
              : 'hydrogen'
          );
        }
      } catch (err) {
        if (!cancelled) {
          onNotify(err instanceof Error ? err.message : 'Interview-Prep konnte nicht geladen werden');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [job.id, onNotify]);

  const handleGenerate = async () => {
    const targetJobId = job.id;
    setGenerating(true);
    try {
      const res = await api.generateInterviewPrep(targetJobId, lang, model);
      if (activeJobIdRef.current !== targetJobId) return;
      if (res.prep) {
        setData(res.prep);
        onNotify(`Interview-Prep für ${job.company} erfolgreich generiert.`);
      }
    } catch (err) {
      if (activeJobIdRef.current === targetJobId) {
        onNotify(err instanceof Error ? err.message : 'Interview-Prep Generierung fehlgeschlagen');
      }
    } finally {
      setGenerating(false);
    }
  };

  const copyToClipboard = async (text: string, sectionKey: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedSection(sectionKey);
      window.setTimeout(() => setCopiedSection(null), 2000);
    } catch {
      onNotify('Kopieren in die Zwischenablage fehlgeschlagen.');
    }
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="interview-prep-title"
    >
      <div
        className="modal-dialog"
        style={{ maxWidth: '820px', width: '92vw', maxHeight: '88vh', display: 'flex', flexDirection: 'column', padding: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--line-hairline)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-toolbar)',
            flexWrap: 'wrap',
            gap: '8px',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '1.25rem' }}>🎤</span>
              <h3 id="interview-prep-title" style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700 }}>
                Interview-Prep: {job.company}
              </h3>
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--ink-secondary)', marginTop: '2px' }}>
              {job.title} · {job.location || 'Standort flexibel'}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', gap: '3px' }} role="group" aria-label="Sprache">
              <button
                type="button"
                className={`seg-btn ${lang === 'de' ? 'is-active' : ''}`}
                style={{ fontSize: '11px', padding: '4px 8px' }}
                onClick={() => setLang('de')}
              >
                DE
              </button>
              <button
                type="button"
                className={`seg-btn ${lang === 'en' ? 'is-active' : ''}`}
                style={{ fontSize: '11px', padding: '4px 8px' }}
                onClick={() => setLang('en')}
              >
                EN
              </button>
            </div>

            <div className="model-chip-group">
              <button
                type="button"
                className={`model-chip ${model === 'hydrogen' ? 'active' : ''}`}
                onClick={() => setModel('hydrogen')}
                title="Gemini 4 Hydrogen (schnell & präzise)"
              >
                <Cpu size={11} strokeWidth={2} />
                Hydrogen
              </button>
              <button
                type="button"
                className={`model-chip ${model === 'argon' ? 'active' : ''}`}
                onClick={() => setModel('argon')}
                title="Gemini 4 Argon (höchste Argumentationstiefe)"
              >
                <Sparkles size={11} strokeWidth={2} />
                Argon
              </button>
              <button
                type="button"
                className={`model-chip ${model === 'neon' ? 'active' : ''}`}
                onClick={() => setModel('neon')}
                title="Gemini 4 Neon (tiefes Reasoning)"
              >
                <Sparkles size={11} strokeWidth={2} />
                Neon
              </button>
            </div>

            {data && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={generating}
                onClick={() => void handleGenerate()}
                style={{ fontSize: '12px', padding: '6px 12px' }}
              >
                <RefreshCw size={12} strokeWidth={1.75} className={generating ? 'animate-spin' : ''} />
                {generating ? 'Generiere …' : 'Neu generieren'}
              </button>
            )}

            <button
              type="button"
              onClick={onClose}
              className="btn-icon"
              aria-label="Fenster schließen (Esc)"
              title="Schließen (Esc)"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {loading ? (
            <div className="empty-state">Lade bestehende Interview-Vorbereitung …</div>
          ) : !data ? (
            <div className="empty-state" style={{ padding: '30px 10px' }}>
              <p style={{ fontWeight: 600, marginBottom: '6px' }}>Noch kein Interview-Spickzettel für diese Stelle vorhanden.</p>
              <p style={{ color: 'var(--ink-secondary)', fontSize: '0.85rem', maxWidth: '50ch', margin: '0 auto 16px' }}>
                KI erstellt anhand der Stellenanzeige, deiner erkannten Stärken und deiner Projekte aus dem Profil
                maßgeschneiderte STAR-Beispiele und ehrliche Brücken-Antworten für Lücken.
              </p>
              <button
                type="button"
                className="btn btn-primary"
                disabled={generating}
                onClick={() => void handleGenerate()}
              >
                <Sparkles size={13} strokeWidth={1.8} />
                {generating ? 'Erstelle Spickzettel …' : 'Jetzt mit KI generieren'}
              </button>
            </div>
          ) : (
            <>
              {/* 60s Pitch */}
              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '14px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <div style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--accent-blue)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>⏱️</span>
                    <span>60-Sekunden Selbstvorstellung (Pitch)</span>
                  </div>
                  <button
                    type="button"
                    className="btn"
                    style={{ fontSize: '11px', padding: '3px 8px' }}
                    onClick={() => void copyToClipboard(data.pitch_60s, 'pitch')}
                  >
                    {copiedSection === 'pitch' ? <Check size={12} color="#10b981" /> : <Copy size={12} />}
                    {copiedSection === 'pitch' ? 'Kopiert!' : 'Kopieren'}
                  </button>
                </div>
                <div style={{ fontSize: '0.88rem', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
                  {data.pitch_60s}
                </div>
              </div>

              {/* STAR Stories */}
              <div>
                <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>⭐</span>
                  <span>STAR-Stories aus deinen realen Projekten</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {data.star_stories.map((star, idx) => (
                    <div
                      key={idx}
                      style={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                        padding: '14px 16px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '6px' }}>
                        <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>
                          Frage {idx + 1}: „{star.question}“
                        </div>
                        <span
                          style={{
                            fontSize: '10.5px',
                            fontWeight: 600,
                            padding: '2px 8px',
                            borderRadius: '4px',
                            background: 'rgba(59, 130, 246, 0.1)',
                            color: 'var(--accent-blue)',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {star.project}
                        </span>
                      </div>
                      <div style={{ fontSize: '0.84rem', lineHeight: 1.5, display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '8px' }}>
                        <div><strong style={{ color: 'var(--ink-secondary)' }}>Situation &amp; Aufgabe:</strong> {star.situation_task}</div>
                        <div><strong style={{ color: 'var(--ink-secondary)' }}>Aktion:</strong> {star.action}</div>
                        <div><strong style={{ color: 'var(--accent-blue)' }}>Ergebnis:</strong> {star.result}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Gap Bridges */}
              <div>
                <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>🌉</span>
                  <span>Ehrliche Brücken-Antworten (Gap-Bridges)</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {data.gap_bridges.map((gb, idx) => (
                    <div
                      key={idx}
                      style={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                        padding: '12px 16px',
                        borderLeft: '4px solid #f59e0b',
                      }}
                    >
                      <div style={{ fontWeight: 600, fontSize: '0.88rem', color: 'var(--amber)', marginBottom: '4px' }}>
                        Kritischer Punkt: {gb.gap}
                      </div>
                      <div style={{ fontSize: '0.84rem', lineHeight: 1.5 }}>
                        „{gb.honest_bridge}“
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Smart Questions */}
              <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '14px 16px' }}>
                <div style={{ fontWeight: 700, fontSize: '0.95rem', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>💡</span>
                  <span>Intelligente Rückfragen an das Fachteam</span>
                </div>
                <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '0.85rem', lineHeight: 1.55 }}>
                  {data.smart_questions.map((q, idx) => (
                    <li key={idx} style={{ marginBottom: '4px' }}>{q}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
