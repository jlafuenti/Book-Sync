import { useState, useEffect } from 'react'

const MOBILE_BREAKPOINT = 768

export default function useIsMobile() {
    const [isMobile, setIsMobile] = useState(() => {
        if (typeof window === 'undefined') return false
        return window.innerWidth <= MOBILE_BREAKPOINT
    })

    useEffect(() => {
        const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`)
        const handleChange = (e) => setIsMobile(e.matches)

        // Set initial value
        setIsMobile(mql.matches)

        // Listen for changes
        mql.addEventListener('change', handleChange)
        return () => mql.removeEventListener('change', handleChange)
    }, [])

    return isMobile
}
