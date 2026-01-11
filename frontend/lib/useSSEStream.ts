"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

export interface ChatMessage {
  id: string
  content: string
  isUser: boolean
  timestamp: Date
  isStreaming?: boolean
  hasReceivedToken?: boolean
}

type SubmitOptions = {
  threadId?: string
}

export function useSSEStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)
  const inFlightRunIdRef = useRef<string | null>(null)

  const stop = useCallback(() => {
    const es = eventSourceRef.current
    if (es && es.readyState !== EventSource.CLOSED) {
      es.close()
    }
    eventSourceRef.current = null
    inFlightRunIdRef.current = null
    setIsLoading(false)
    setMessages((prev) => prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)))
  }, [])

  const submit = useCallback(
    async (userText: string, opts?: SubmitOptions) => {
      if (!userText.trim() || isLoading) return

      const userMsg: ChatMessage = {
        id: `${Date.now()}`,
        content: userText,
        isUser: true,
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, userMsg])

      const botMsg: ChatMessage = {
        id: `${Date.now()}-bot`,
        content: "",
        isUser: false,
        timestamp: new Date(),
        isStreaming: true,
        hasReceivedToken: false,
      }
      setMessages((prev) => [...prev, botMsg])

      setIsLoading(true)

      const threadId = opts?.threadId ?? `user-${Date.now()}`
      const query = encodeURIComponent(userText)

      let gotAnyToken = false
      let isCompleted = false

      const cleanup = () => {
        isCompleted = true
        setIsLoading(false)
        setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, isStreaming: false } : m)))
        inFlightRunIdRef.current = null
      }

      try {
        const es = new EventSource(`/api/stream?query=${query}&thread_id=${threadId}`)
        eventSourceRef.current = es
        inFlightRunIdRef.current = threadId

        es.addEventListener("token", (e) => {
          if (isCompleted) return
          try {
            const payload = JSON.parse(e.data as string)
            const piece = payload?.content ?? ""
            if (piece) {
              gotAnyToken = true
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === botMsg.id
                    ? { ...m, content: m.content + piece, hasReceivedToken: true }
                    : m,
                ),
              )
            }
          } catch (err) {
            // ignore malformed chunks
          }
        })

        es.addEventListener("final", (e) => {
          if (isCompleted) return
          try {
            const { answer } = JSON.parse((e as MessageEvent).data as string)
            if (answer) {
              setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, content: answer, isStreaming: false } : m)))
            }
          } catch {
            // ignore
          }
          cleanup()
        })

        es.addEventListener("done", () => {
          if (isCompleted) return
          cleanup()
        })

        es.onerror = () => {
          if (isCompleted || gotAnyToken) {
            cleanup()
            return
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === botMsg.id
                ? {
                    ...m,
                    content: "죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    isStreaming: false,
                  }
                : m,
            ),
          )
          cleanup()
        }

        // Optional: local timeout guard only if no token has arrived
        setTimeout(() => {
          if (!isCompleted && !gotAnyToken) {
            stop()
          }
        }, 30_000)
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === botMsg.id
              ? {
                  ...m,
                  content: "죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                  isStreaming: false,
                }
              : m,
          ),
        )
        setIsLoading(false)
      }
    },
    [isLoading],
  )

  // API similar to LangGraph useStream
  return useMemo(
    () => ({
      messages,
      isLoading,
      submit,
      stop,
    }),
    [messages, isLoading, submit, stop],
  )
}


