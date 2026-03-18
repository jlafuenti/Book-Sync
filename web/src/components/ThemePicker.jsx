import { useState } from 'react'
import { THEMES, THEME_SLUGS } from '../themes'
import { useTheme } from '../ThemeContext'
import { updateMe } from '../api'

export function ThemePicker() {
    const { theme, setTheme } = useTheme()
    const [saving, setSaving] = useState(false)

    const handleSelect = async (slug) => {
        if (slug === theme || saving) return
        setTheme(slug)              // instant visual update
        setSaving(true)
        try {
            await updateMe({ theme: slug })
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="theme-picker" aria-label="Colour theme">
            {THEME_SLUGS.map(slug => (
                <button
                    key={slug}
                    className={`theme-swatch${theme === slug ? ' active' : ''}`}
                    style={{ '--swatch-color': THEMES[slug].accent }}
                    onClick={() => handleSelect(slug)}
                    title={THEMES[slug].label}
                    aria-pressed={theme === slug}
                />
            ))}
        </div>
    )
}
