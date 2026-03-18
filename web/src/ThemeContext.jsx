import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { applyTheme, DEFAULT_THEME } from './themes'

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
    const [theme, setThemeState] = useState(
        () => localStorage.getItem('tandem_theme') || DEFAULT_THEME
    )

    // Apply stored theme before first paint
    useEffect(() => {
        applyTheme(theme)
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    const setTheme = useCallback((slug) => {
        setThemeState(slug)
        localStorage.setItem('tandem_theme', slug)
        applyTheme(slug)
    }, [])

    return (
        <ThemeContext.Provider value={{ theme, setTheme }}>
            {children}
        </ThemeContext.Provider>
    )
}

export function useTheme() {
    return useContext(ThemeContext)
}
