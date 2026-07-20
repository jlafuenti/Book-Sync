import React, { useState } from 'react'
import MatchTab from './MatchTab'
import { updateEbookMetadata, updateAudiobookMetadata, applyRemoteCover } from '../api'

export default function BulkMatchModal({ books, bookType, onClose, onUpdate }) {
    const [index, setIndex] = useState(0)
    const [applying, setApplying] = useState(false)
    const [done, setDone] = useState(false)
    const [appliedCount, setAppliedCount] = useState(0)

    const current = books[index]

    const advance = () => {
        if (index + 1 >= books.length) {
            setDone(true)
        } else {
            setIndex(i => i + 1)
        }
    }

    const handleApply = async (payload) => {
        const { coverUrl, ...textFields } = payload
        setApplying(true)
        try {
            const updateFn = bookType === 'ebook' ? updateEbookMetadata : updateAudiobookMetadata
            if (Object.keys(textFields).length > 0) {
                await updateFn(current.id, textFields)
                onUpdate(current.id, textFields)
            }
            if (coverUrl) {
                await applyRemoteCover(bookType, current.id, coverUrl)
            }
            setAppliedCount(c => c + 1)
            advance()
        } catch (err) {
            alert('Failed to apply: ' + err.message)
        } finally {
            setApplying(false)
        }
    }

    return (
        <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.7)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
            <div className="card" style={{ width: '90%', maxWidth: '860px', maxHeight: '90vh', overflowY: 'auto', padding: '24px' }}>
                {done ? (
                    <div style={{ textAlign: 'center', padding: '40px 0' }}>
                        <div style={{ fontSize: '2.5rem', marginBottom: '12px' }}>✅</div>
                        <h3>Done — matched {appliedCount} of {books.length} book{books.length !== 1 ? 's' : ''}</h3>
                        <button className="btn btn-primary" onClick={onClose} style={{ marginTop: '16px' }}>Close</button>
                    </div>
                ) : (
                    <>
                        {/* Header */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                            <div>
                                <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '4px' }}>
                                    Book {index + 1} of {books.length}
                                </div>
                                <h3 style={{ margin: 0 }}>{current.title || current.filename}</h3>
                                {current.author && <div style={{ color: 'var(--text-secondary)', marginTop: '2px' }}>by {current.author}</div>}
                                {current.series && (
                                    <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '2px' }}>
                                        {current.series}{current.series_index != null ? ` #${current.series_index}` : ''}
                                    </div>
                                )}
                            </div>
                            <button className="btn btn-secondary" onClick={onClose}>✕ Cancel</button>
                        </div>

                        {/* Progress bar */}
                        <div style={{ height: '4px', background: 'var(--border)', borderRadius: '2px', marginBottom: '20px' }}>
                            <div style={{
                                height: '100%',
                                width: `${(index / books.length) * 100}%`,
                                background: 'var(--accent)',
                                borderRadius: '2px',
                                transition: 'width 0.3s'
                            }} />
                        </div>

                        {/* MatchTab — key forces reset when book changes */}
                        <div style={{ opacity: applying ? 0.5 : 1, pointerEvents: applying ? 'none' : 'auto' }}>
                            <MatchTab key={current.id} currentData={current} onApply={handleApply} bookType={bookType} />
                        </div>

                        {/* Footer */}
                        <div style={{
                            display: 'flex', justifyContent: 'flex-end', marginTop: '16px',
                            borderTop: '1px solid var(--border)', paddingTop: '16px'
                        }}>
                            <button className="btn btn-secondary" onClick={advance} disabled={applying}>
                                Skip →
                            </button>
                        </div>
                    </>
                )}
            </div>
        </div>
    )
}
