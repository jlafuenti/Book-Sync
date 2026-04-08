import React, { useState, useEffect, useRef } from 'react'
import '../pages/FilterPill.css'

export default function FilterPill({ label, value, options, onChange }) {
    const [open, setOpen] = useState(false)
    const ref = useRef(null)

    useEffect(() => {
        if (!open) return
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) setOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [open])

    return (
        <div className="filter-pill-wrap" ref={ref}>
            <button
                className={`library-filter-pill filter-pill-btn${value ? ' active' : ''}${!value && open ? ' filter-pill-open' : ''}`}
                onClick={() => setOpen(o => !o)}
            >
                {value || label}
                {value ? (
                    <span
                        className="filter-pill-x"
                        onClick={e => { e.stopPropagation(); onChange(''); setOpen(false) }}
                        title="Clear filter"
                    >✕</span>
                ) : (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="10" height="10" style={{ marginLeft: 2, flexShrink: 0 }}>
                        <polyline points="6 9 12 15 18 9" />
                    </svg>
                )}
            </button>
            {open && options.length > 0 && (
                <div className="filter-pill-dropdown">
                    {value && (
                        <button
                            className="filter-pill-option"
                            onClick={() => { onChange(''); setOpen(false) }}
                            style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}
                        >
                            Clear filter
                        </button>
                    )}
                    {value && <hr className="filter-pill-divider" />}
                    {options.map(o => (
                        <button
                            key={o}
                            className={`filter-pill-option${o === value ? ' active' : ''}`}
                            onClick={() => { onChange(o); setOpen(false) }}
                        >
                            {o}
                            {o === value && (
                                <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12">
                                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
                                </svg>
                            )}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
