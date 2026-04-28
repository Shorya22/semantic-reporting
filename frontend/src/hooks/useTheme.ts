import { useEffect, useState } from 'react'
import { useStore } from '../store'

/**
 * Apply the chosen theme to ``<html data-theme="...">`` and keep it in sync
 * with the OS preference when the user picked ``'system'``.
 *
 * Three values:
 *   * ``'dark'``   – force the dark palette
 *   * ``'light'``  – force the light palette
 *   * ``'system'`` – follow ``prefers-color-scheme`` and re-apply on change
 *
 * The CSS variables in ``index.css`` change based on the resolved theme,
 * so flipping this attribute is enough to retheme the whole app.
 */
export function useTheme(): void {
  const theme = useStore((s) => s.theme)

  useEffect(() => {
    const root = document.documentElement
    const apply = (resolved: 'dark' | 'light') => {
      root.setAttribute('data-theme', resolved)
      root.style.colorScheme = resolved
    }

    if (theme === 'system') {
      const mql = window.matchMedia('(prefers-color-scheme: dark)')
      apply(mql.matches ? 'dark' : 'light')
      const onChange = (e: MediaQueryListEvent) => apply(e.matches ? 'dark' : 'light')
      mql.addEventListener('change', onChange)
      return () => mql.removeEventListener('change', onChange)
    }

    apply(theme)
  }, [theme])
}


/**
 * Resolve the user's theme choice to the concrete ``'dark' | 'light'``
 * that the UI is currently rendering. Components that need to swap
 * colors at render time (charts, custom canvas drawing, etc.) call
 * this — re-rendering whenever the resolved theme changes.
 */
export function useResolvedTheme(): 'dark' | 'light' {
  const choice = useStore((s) => s.theme)
  const [systemDark, setSystemDark] = useState<boolean>(() =>
    typeof window === 'undefined'
      ? true
      : window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  useEffect(() => {
    if (choice !== 'system') return
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [choice])

  if (choice === 'dark')  return 'dark'
  if (choice === 'light') return 'light'
  return systemDark ? 'dark' : 'light'
}
