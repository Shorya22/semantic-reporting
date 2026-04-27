import { useEffect } from 'react'
import { api } from '../api/client'
import { useStore } from '../store'
import { LlmProvider } from '../types'

/**
 * Hydrate the app from the backend on first mount.
 *
 * Order matters:
 *   1. /preferences          — server-saved prefs override (or seed) localStorage
 *   2. /connections          — list of persisted DB sessions; pick the active one
 *   3. /conversations        — sidebar list
 *   4. /conversations/{id}   — load messages for the active conversation
 *
 * Failures are swallowed individually so a missing piece never blocks
 * the rest of the boot — the app still works in pure ad-hoc mode if the
 * app DB is empty.
 */
export function useHydrate() {
  useEffect(() => {
    let cancelled = false
    const store = useStore.getState()

    async function run() {
      // 1. Server prefs: source of truth for cross-device, and seeds the
      // store on first run if localStorage was empty.
      try {
        const prefs = await api.getPreferences()
        if (cancelled) return
        if (prefs.model)    store.setModel(prefs.model)
        if (prefs.provider) store.setProvider(prefs.provider as LlmProvider)
        if (prefs.active_connection_id) {
          // Only adopt the server's choice if local one is unset, so user
          // intent in this tab wins over a stale server pref.
          if (!useStore.getState().activeSessionId) {
            store.setActiveSession(prefs.active_connection_id)
          }
        }
        if (prefs.active_conversation_id) {
          if (!useStore.getState().activeConversationId) {
            store.setActiveConversation(prefs.active_conversation_id)
          }
        }
      } catch { /* missing prefs is fine on first boot */ }

      // 2. Connections — rehydrate the in-memory list from persisted truth.
      try {
        const conns = await api.listConnections()
        if (cancelled) return
        store.setSessions(conns)

        // If the previously-active session no longer exists on the server,
        // clear the local pointer so the UI doesn't render an orphan.
        const active = useStore.getState().activeSessionId
        if (active && !conns.some((c) => c.session_id === active)) {
          store.setActiveSession(null)
        }
      } catch { /* empty list is fine */ }

      // 3. Conversations — populate sidebar.
      try {
        const conversations = await api.listConversations()
        if (cancelled) return
        store.setConversations(conversations)

        // Validate active conversation pointer.
        const active = useStore.getState().activeConversationId
        if (active && !conversations.some((c) => c.id === active)) {
          store.setActiveConversation(null)
          store.setAnalyses([])
        }
      } catch { /* */ }

      // 4. Active conversation messages — restore the workspace contents.
      const activeConv = useStore.getState().activeConversationId
      if (activeConv) {
        try {
          const { conversation, messages } = await api.getConversation(activeConv)
          if (cancelled) return
          store.loadAnalysesFromMessages(messages)

          // Adopt the conversation's bound connection if no session is active.
          if (!useStore.getState().activeSessionId && conversation.connection_id) {
            store.setActiveSession(conversation.connection_id)
          }
        } catch { /* */ }
      }

      if (!cancelled) store.setHydrated(true)
    }

    run()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}


/**
 * Whenever the active conversation changes (after hydration), pull its
 * messages and replace the analyses list. Skips the very first switch
 * if useHydrate already loaded them.
 */
export function useConversationSync() {
  const activeConversationId = useStore((s) => s.activeConversationId)
  const hydrated             = useStore((s) => s.hydrated)

  useEffect(() => {
    if (!hydrated || !activeConversationId) return

    let cancelled = false
    api.getConversation(activeConversationId)
      .then(({ messages }) => {
        if (cancelled) return
        useStore.getState().loadAnalysesFromMessages(messages)
      })
      .catch(() => {
        if (cancelled) return
        useStore.getState().setAnalyses([])
      })

    return () => { cancelled = true }
  }, [hydrated, activeConversationId])
}


/**
 * Persist user preference changes back to the server, debounced. Called
 * by App.tsx whenever model/provider/active pointers change so the next
 * device to log in (or the next refresh on a clean cache) gets the
 * latest choices.
 */
export function usePreferenceSync() {
  const model                = useStore((s) => s.model)
  const provider             = useStore((s) => s.provider)
  const activeSessionId      = useStore((s) => s.activeSessionId)
  const activeConversationId = useStore((s) => s.activeConversationId)
  const hydrated             = useStore((s) => s.hydrated)

  useEffect(() => {
    if (!hydrated) return
    const t = setTimeout(() => {
      api.updatePreferences({
        model,
        provider,
        active_connection_id:   activeSessionId,
        active_conversation_id: activeConversationId,
      }).catch(() => {})
    }, 250)
    return () => clearTimeout(t)
  }, [hydrated, model, provider, activeSessionId, activeConversationId])
}
