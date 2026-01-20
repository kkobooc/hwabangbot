"use client"
import type React from "react"

import { useState, useRef, useEffect, memo } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Send, Brush, Droplet, Frame } from "lucide-react"
import ReactMarkdown from "react-markdown"
import Image from "next/image"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"

function prepareMarkdownForRender(raw: string, isStreaming: boolean) {
  // If content is wrapped in a top-level ```md/markdown/gfm fence, unwrap it.
  // Works for fully closed fences and partially streamed content without a closing fence.
  const headerMatch = raw.match(/^\s*```(\w+)?\s*\n/)
  if (headerMatch) {
    const lang = (headerMatch[1] || "").toLowerCase()
    if (lang === "markdown" || lang === "md" || lang === "gfm") {
      const withoutHeader = raw.replace(/^\s*```(\w+)?\s*\n/, "")
      const closingIndex = withoutHeader.lastIndexOf("\n```")
      if (closingIndex !== -1) {
        // Fully wrapped; strip the trailing fence too
        return withoutHeader.slice(0, closingIndex)
      }
      // No closing fence yet (streaming); render the body as-is
      return withoutHeader
    }
  }
  // While streaming, adding a trailing newline can help parsers with partial blocks
  return isStreaming ? raw + "\n" : raw
}

const rotatingLoadingMessages = [
  "화방넷에 수많은 미술재료들을 살펴보고 있어요.🎨",
  "영감이 가득한 답변을 준비 중이에요. 🎨",
  "화방넷이 미술 재료 사이에서 자료 수집 중이에요. 🎨",
]

function LoadingIndicator() {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const id = setInterval(() => {
      setIndex((i) => (i + 1) % rotatingLoadingMessages.length)
    }, 1400)
    return () => clearInterval(id)
  }, [])

  const text = rotatingLoadingMessages[index]

  return (
    <span className="block mt-2 text-gray-900 animate-pulse text-sm" aria-live="polite">
      {text}
    </span>
  )
}

interface Message {
  id: string
  content: string
  isUser: boolean
  timestamp: Date
  isStreaming?: boolean
}

const MAX_USAGE_PER_SESSION = 20

function getUsageCount(): number {
  if (typeof window === "undefined") return 0
  const stored = sessionStorage.getItem("usageCount")
  return stored ? parseInt(stored, 10) : 0
}

function incrementUsageCount(): number {
  if (typeof window === "undefined") return 0
  const current = getUsageCount()
  const newCount = current + 1
  sessionStorage.setItem("usageCount", newCount.toString())
  return newCount
}

function Header({ usageCount }: { usageCount: number }) {
  return (
    <div className="px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Image src="/logo.png" alt="화방넷 로고" width={32} height={32} className="rounded-lg" />
          <h1 className="text-lg md:text-lg font-medium text-[#1B1C1D]">화방넷 AI 큐레이터</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="relative hidden md:block">
            <div
              className="bg-gradient-to-b from-[#ff8431] to-[#ff5c01] rounded-[8px] p-[11px] text-white text-[12px] leading-[18px] tracking-[-0.1px]"
              style={{
                background: "linear-gradient(180deg, #ff8431 5.696%, #ff5c01 100%)",
              }}
            >
              <p className="mb-0">화방넷 AI 답변은 하루에 최대 20번 가능합니다.</p>
              <p className="mb-0">횟수가 초과 됐다면 내일 새로운 답변을 받아보세요!</p>
            </div>
            <div
              className="absolute top-1/2 -translate-y-1/2 right-0 translate-x-full w-0 h-0"
              style={{
                borderTop: "6px solid transparent",
                borderBottom: "6px solid transparent",
                borderLeft: "6px solid #ff8431",
              }}
            />
          </div>
          <div className="border border-[#dbdbdb] rounded-[36px] flex items-center justify-center gap-[4px] px-5 py-1">
            <div className="relative shrink-0 w-5 h-5">
              <Image src="/logo.png" alt="" width={20} height={20} className="w-full h-full" />
            </div>
            <p className="font-medium leading-7 text-sm md:text-base text-black tracking-[-0.1px]">
              <span className="text-[#ff5c01]">{usageCount}</span>
              <span className="text-[#999999]"> / </span>
              <span className="text-[#999999]">{MAX_USAGE_PER_SESSION}</span>
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ArtSuppliesChatbot() {
  const [messages, setMessages] = useState<Message[]>([])
  const [inputValue, setInputValue] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [selectedButton, setSelectedButton] = useState<string>("수채화 추천")
  const [usageCount, setUsageCount] = useState(0)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const lastMessageIdRef = useRef<string | null>(null)
  const threadIdRef = useRef<string>(`user-${Date.now()}`)

  // 초기 사용 횟수 로드
  useEffect(() => {
    setUsageCount(getUsageCount())
  }, [])

  const scrollToBottom = (smooth: boolean) => {
    messagesEndRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" })
  }

  // 새 메시지가 추가될 때만 부드럽게 스크롤. 토큰 단위 업데이트에선 스크롤하지 않음
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (!last) return
    if (lastMessageIdRef.current !== last.id) {
      lastMessageIdRef.current = last.id
      scrollToBottom(true)
    }
  }, [messages.length])

  const suggestedQuestions = ["수채화 추천", "물감 비교", "캔버스 가이드"]

  const handleMarkdownClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    const btn = target.closest('[data-question]') as HTMLElement | null
    if (btn) {
      e.preventDefault()
      const q = btn.getAttribute('data-question') || btn.textContent || ''
      setInputValue(q.trim())
    }
  }

  const handleSuggestedQuestion = (question: string, buttonName: string) => {
    setSelectedButton(buttonName)
    setInputValue(question)
  }

  const callStreamingAPI = async (userMessage: string) => {
    // 중복 호출 방지
    if (isLoading) {
      console.log("Already loading, ignoring duplicate call")
      return
    }

    // 사용자 메시지 추가
    const userMsg: Message = {
      id: Date.now().toString(),
      content: userMessage,
      isUser: true,
      timestamp: new Date(),
    }
    setMessages((prev) => [...prev, userMsg])

    setIsLoading(true)

    const botMsg: Message = {
      id: (Date.now() + 1).toString(),
      content: "",
      isUser: false,
      timestamp: new Date(),
      isStreaming: true,
    }

    setMessages((prev) => [...prev, botMsg])

    let eventSource: EventSource | null = null
    let gotAnyToken = false
    let isCompleted = false

    let timeoutId: NodeJS.Timeout | null = null

    const cleanup = () => {
      if (timeoutId) {
        clearTimeout(timeoutId)
        timeoutId = null
      }
      if (eventSource && eventSource.readyState !== EventSource.CLOSED) {
        console.log("Closing EventSource")
        eventSource.close()
      }
      isCompleted = true
      setIsLoading(false)
      setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, isStreaming: false } : m)))
    }

    try {
      const query = encodeURIComponent(userMessage)
      const thread_id = threadIdRef.current
      eventSource = new EventSource(`/api/stream?query=${query}&thread_id=${thread_id}`)

      eventSource.onopen = () => {
        console.log("SSE opened")
      }

      // Token handler with micro-batching to avoid flicker from partial HTML/Markdown
      let pending = ""
      let scheduled = false
      const scheduleFlush = () => {
        if (scheduled) return
        scheduled = true
        setTimeout(() => {
          if (pending) {
            const chunk = pending
            pending = ""
            setMessages((prev) =>
              prev.map((msg) => (msg.id === botMsg.id ? { ...msg, content: msg.content + chunk } : msg)),
            )
          }
          scheduled = false
          if (pending) scheduleFlush()
        }, 100)
      }

      eventSource.addEventListener("token", (e) => {
        if (isCompleted) return

        let payload
        try {
          payload = JSON.parse(e.data)
        } catch (err) {
          console.error("SSE JSON parse error:", e.data, err)
          return
        }

        const piece = payload?.content ?? ""
        if (piece) {
          gotAnyToken = true
          pending += piece
          scheduleFlush()
        }
      })

      eventSource.addEventListener("final", (e) => {
        if (isCompleted) return

        console.log("Received final event")
        try {
          const { answer } = JSON.parse(e.data)
          if (answer) {
            setMessages((prev) =>
              prev.map((msg) => (msg.id === botMsg.id ? { ...msg, content: answer, isStreaming: false } : msg)),
            )
          }
        } catch (err) {
          console.error("Final event parse error:", err)
        }
        cleanup()
      })

      eventSource.addEventListener("done", () => {
        if (isCompleted) return

        console.log("Received done event")
        // Flush any remaining pending content before finishing
        if (pending) {
          const finalChunk = pending
          pending = ""
          setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, content: m.content + finalChunk } : m)))
        }
        setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, isStreaming: false } : m)))
        cleanup()
      })

      eventSource.onerror = (e) => {
        console.warn("EventSource error:", e)
        if (isCompleted || gotAnyToken) {
          cleanup()
          return
        }

        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsg.id
              ? {
                  ...msg,
                  content: "죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                  isStreaming: false,
                }
              : msg,
          ),
        )
        cleanup()
      }

      timeoutId = setTimeout(() => {
        // 타임아웃은 첫 토큰을 전혀 받지 못한 경우에만 동작
        if (!isCompleted && !gotAnyToken) {
          console.warn("SSE timeout — closing")
          setMessages((prev) => prev.map((m) => (m.id === botMsg.id ? { ...m, isStreaming: false } : m)))
          cleanup()
        }
      }, 30_000) // 30초로 단축
    } catch (error) {
      console.error("API Error:", error)
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === botMsg.id
            ? {
                ...msg,
                content: "죄송합니다. 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                isStreaming: false,
              }
            : msg,
        ),
      )
      setIsLoading(false)
    }
  }

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isLoading) {
      console.log("Ignoring send message - empty input or already loading")
      return
    }

    // 사용 횟수 확인
    const currentUsage = getUsageCount()
    if (currentUsage >= MAX_USAGE_PER_SESSION) {
      alert(`세션당 최대 ${MAX_USAGE_PER_SESSION}회까지 사용 가능합니다. 페이지를 새로고침하면 다시 사용할 수 있습니다.`)
      return
    }

    const message = inputValue.trim()
    setInputValue("")

    // 사용 횟수 증가
    const newCount = incrementUsageCount()
    setUsageCount(newCount)

    await callStreamingAPI(message)
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!isLoading && inputValue.trim()) {
        handleSendMessage()
      }
    }
  }

  if (messages.length === 0) {
    return (
      <div className="flex flex-col h-screen bg-white">
        <Header usageCount={usageCount} />

        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className="flex flex-col items-center gap-10">
            <div className="relative w-[86px] h-[86px]">
              <Image src="/logo.png" alt="화방넷 로고" width={86} height={86} />
            </div>

            <div className="flex flex-col items-center gap-[11px]">
              <p className="text-[16px] md:text-[18px] leading-6 text-center tracking-[-0.2px] text-[#1B1C1D]">
                국내 최초 AI 미술용품 전문가, 화방넷 AI 큐레이터
              </p>
              <h2 className="text-[26px] md:text-[30px] leading-10 font-medium text-center tracking-[-0.2px] text-[#FF8431]">
                미술용품에 관해 무엇이든 물어보세요
              </h2>
            </div>

            <div className="flex flex-row items-center gap-[6px]">
              <Button
                onClick={() => handleSuggestedQuestion("수채화 입문자에게 추천하는 물감은?", "수채화 추천")}
                className={`flex items-center gap-2.5 px-[22px] py-2 h-10 border rounded-[30px] text-[13px] md:text-[14px] leading-6 tracking-[-0.2px] ${
                  selectedButton === "수채화 추천"
                    ? "bg-[#FF8431] hover:bg-[#FF8431]/90 text-white border-[#FF8431]"
                    : "bg-white hover:bg-gray-50 text-[#1B1C1D] border-[#C4C7C5]"
                }`}
              >
                <Brush
                  className={`w-[26px] h-[26px] ${selectedButton === "수채화 추천" ? "text-white" : "text-[#FF8431]"}`}
                />
                수채화 추천
              </Button>
              <Button
                onClick={() => handleSuggestedQuestion("유화와 아크릴 물감의 차이점", "물감 비교")}
                className={`flex items-center gap-2.5 px-[22px] py-2 h-10 border rounded-[30px] text-[13px] md:text-[14px] leading-6 tracking-[-0.2px] ${
                  selectedButton === "물감 비교"
                    ? "bg-[#FF8431] hover:bg-[#FF8431]/90 text-white border-[#FF8431]"
                    : "bg-white hover:bg-gray-50 text-[#1B1C1D] border-[#C4C7C5]"
                }`}
              >
                <Droplet className={`w-6 h-6 ${selectedButton === "물감 비교" ? "text-white" : "text-[#FF8431]"}`} />
                물감 비교
              </Button>
              <Button
                onClick={() => handleSuggestedQuestion("캔버스 크기별 용도 추천", "캔버스 가이드")}
                className={`flex items-center gap-2.5 px-[22px] py-2 h-10 border rounded-[30px] text-[13px] md:text-[14px] leading-6 tracking-[-0.2px] ${
                  selectedButton === "캔버스 가이드"
                    ? "bg-[#FF8431] hover:bg-[#FF8431]/90 text-white border-[#FF8431]"
                    : "bg-white hover:bg-gray-50 text-[#1B1C1D] border-[#C4C7C5]"
                }`}
              >
                <Frame className={`w-6 h-6 ${selectedButton === "캔버스 가이드" ? "text-white" : "text-[#FF8431]"}`} />
                캔버스 가이드
              </Button>
            </div>
          </div>
        </div>

        <div className="flex flex-col justify-end items-center px-4 h-[100px] min-h-[70px] absolute bottom-0 left-0 right-0 bg-white pb-4">
          <div className="w-full max-w-[680px]">
            <div className="relative bg-white border border-gray-200 rounded-full shadow-sm">
              <Input
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="수채화 입문자에게 추천하는 물감은?"
                className="w-full h-14 pl-6 pr-16 text-sm md:text-base border-0 rounded-full focus:ring-0 focus:outline-none"
                disabled={isLoading || usageCount >= MAX_USAGE_PER_SESSION}
              />
              <Button
                onClick={handleSendMessage}
                disabled={!inputValue.trim() || isLoading || usageCount >= MAX_USAGE_PER_SESSION}
                className="absolute right-2 top-2 h-10 w-10 p-0 bg-[#FF8431] hover:bg-[#FF8431]/90 rounded-full disabled:opacity-50"
              >
                <Send className="w-5 h-5" />
              </Button>
            </div>
            <p className="text-center text-xs text-gray-400 mt-2">AI 큐레이터가 제공하는 답변이 완벽하지 않을 수 있으니 참고 기준으로 활용해 주세요.</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen bg-white">
      <Header usageCount={usageCount} />

      <div className="flex-1 overflow-hidden">
        <ScrollArea className="h-full" ref={scrollAreaRef}>
          <div className="max-w-4xl mx-auto pb-32">
            {messages.map((message) => (
              <MemoMessageItem key={message.id} message={message} onMarkdownClick={handleMarkdownClick} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>
      </div>

      <div className="flex flex-col justify-end items-center px-4 h-[100px] min-h-[70px] absolute bottom-0 left-0 right-0 bg-white pb-4">
        <div className="w-full max-w-4xl">
          <div className="relative">
            <Input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="메시지를 입력하세요..."
              className="w-full h-12 pl-4 pr-12 text-sm md:text-base border-gray-300 rounded-full shadow-sm focus:border-orange-500 focus:ring-orange-500"
              disabled={isLoading || usageCount >= MAX_USAGE_PER_SESSION}
            />
            <Button
              onClick={handleSendMessage}
              disabled={!inputValue.trim() || isLoading || usageCount >= MAX_USAGE_PER_SESSION}
              className="absolute right-2 top-2 h-8 w-8 p-0 bg-orange-500 hover:bg-orange-600 rounded-full disabled:opacity-50"
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
          <p className="text-center text-xs text-gray-400 mt-2">AI 큐레이터가 제공하는 답변이 완벽하지 않을 수 있으니 참고 기준으로 활용해 주세요.</p>
        </div>
      </div>
    </div>
  )
}

const MemoMessageItem = memo(function MessageItem({
  message,
  onMarkdownClick,
}: {
  message: Message
  onMarkdownClick: (e: React.MouseEvent<HTMLDivElement>) => void
}) {
  const mdRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const root = mdRef.current
    if (!root) return
    const imgs = root.querySelectorAll<HTMLImageElement>('a > img.thumb')
    imgs.forEach((img) => {
      const a = img.parentElement as HTMLElement | null
      if (a) a.classList.add('js-thumb-link')
    })
    
    // 모바일에서 테이블의 th를 td의 data-label로 설정
    const tables = root.querySelectorAll('table')
    tables.forEach((table) => {
      const thead = table.querySelector('thead')
      if (!thead) return
      
      const ths = Array.from(thead.querySelectorAll('th'))
      const tbody = table.querySelector('tbody')
      if (!tbody) return
      
      const rows = tbody.querySelectorAll('tr')
      rows.forEach((row) => {
        const tds = row.querySelectorAll('td')
        tds.forEach((td, index) => {
          if (ths[index]) {
            td.setAttribute('data-label', ths[index].textContent || '')
          }
        })
      })
    })
  }, [message.content])
  return (
    <div className={`py-6 px-4 ${message.isUser ? "bg-white" : "bg-gray-50"}`}>
      {message.isUser ? (
        <div className="flex justify-end">
          <div className="bg-[#FF8431] text-white px-4 py-2 text-sm md:text-base leading-6 max-w-[75%]" style={{ borderRadius: "28px 2px 28px 28px" }}>
            {message.content}
          </div>
        </div>
      ) : (
        <div className="flex gap-4">
          <div className="flex-shrink-0">
            <Image src="/logo.png" alt="화방넷 로고" width={32} height={32} className="rounded-lg" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="text-gray-900 leading-relaxed text-sm md:text-base">
              <div ref={mdRef} className="markdown-body prose prose-neutral max-w-none prose-sm md:prose-base" onClick={onMarkdownClick}>
              <ReactMarkdown
              
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                allowedElements={[
                  // headings
                  "h1",
                  "h2",
                  "h3",
                  "h4",
                  "h5",
                  "h6",
                  // semantic containers
                  "section",
                  "article",
                  // text blocks
                  "p",
                  "strong",
                  "s",
                  "em",
                  "blockquote",
                  "hr",
                  // lists
                  "ul",
                  "ol",
                  "li",
                  // code
                  "code",
                  "pre",
                  // links & images
                  "a",
                  "img",
                  // common html
                  "div",
                  "span",
                  "br",
                  // tables
                  "table",
                  "thead",
                  "tbody",
                  "tr",
                  "th",
                  "td",
                ]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="mt-4 mb-2 first:mt-0 text-gray-900 font-bold">{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="mt-4 mb-2 first:mt-0 text-gray-900 font-bold">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="mt-4 mb-2 first:mt-0 text-gray-900 font-bold">{children}</h3>
                  ),
                  h4: ({ children }) => (
                    <h4 className="mt-4 mb-2 first:mt-0 text-gray-900 font-bold">{children}</h4>
                  ),
                  h5: ({ children }) => (
                    <h5 className="mt-4 mb-2 first:mt-0 text-orange-600 font-semibold">{children}</h5>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-orange-600">{children}</strong>
                  ),
                  ul: ({ children }) => <ul className="list-disc list-inside space-y-1 my-3">{children}</ul>,
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside space-y-1 my-3">{children}</ol>
                  ),
                  p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                  table: ({ children }) => (
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse">{children}</table>
                    </div>
                  ),
                  thead: ({ children }) => <thead className="hidden md:table-header-group">{children}</thead>,
                  tbody: ({ children }) => <tbody>{children}</tbody>,
                  tr: ({ children, ...props }) => <tr className="block md:table-row border md:border-none mb-2 md:mb-0 rounded md:rounded-none p-2 md:p-0" {...props}>{children}</tr>,
                  th: ({ children }) => <th className="hidden md:table-cell px-4 py-2 text-left font-semibold border border-gray-300">{children}</th>,
                  td: ({ children, ...props }) => (
                    <td 
                      className="block md:table-cell px-0 md:px-4 py-1 md:py-2 text-left border-0 md:border border-gray-300" 
                      {...props}
                    >
                      {children}
                    </td>
                  ),
                  img: ({ src, alt, ...props }) => (
                    <img
                      src={src || "/placeholder.svg"}
                      alt={alt}
                      className="h-auto max-h-64 object-contain rounded-lg shadow-sm max-w-[33%]"
                      loading="lazy"
                      {...props}
                    />
                  ),
                }}
              >
                {prepareMarkdownForRender(message.content, Boolean(message.isStreaming))}
              </ReactMarkdown>
              </div>
              {message.isStreaming && message.content.length === 0 && <LoadingIndicator />}
            </div>
          </div>
        </div>
      )}
    </div>
  )
})

