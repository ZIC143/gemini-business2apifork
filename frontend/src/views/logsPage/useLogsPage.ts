import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { logsApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { AdminLogStats, LogEntry } from '@/types/api'

type AdminLogGroup = {
  id?: string
  request_id?: string
  start_time?: string
  status?: string
  account_id?: string
  model?: string
  lane?: string
  terminal_kind?: string
  started_at?: string
  ended_at?: string
  user_preview?: string
  assistant_preview?: string
  row_ids?: string[]
  events?: Array<{
    time?: string
    type?: string
    status?: string
    content?: string
  }>
}

type ParsedLogEntry = LogEntry & {
  rowId: string
  tags: string[]
  accountId: string
  text: string
  reqId: string
  layer: LogLayer
  lane: string
  model: string
  kind: string
  stage: string
  servedLabel: string
}

type GroupedLog = {
  id: string
  logs: ParsedLogEntry[]
  status: string
  accountId: string
  model: string
  lane: string
  terminalKind: string
  startedAt: string
  endedAt: string
  userPreview: string
  assistantPreview: string
}

type GroupedLogState = {
  ungrouped: ParsedLogEntry[]
  groups: GroupedLog[]
}

type LogLayer = 'system' | 'chat' | 'reverse' | 'other'

type GroupLayerSection = {
  key: LogLayer
  label: string
  badgeClass: string
  logs: ParsedLogEntry[]
}

type LogsUiState = {
  level: string
  search: string
  limit: number
  rawView: boolean
  detailMode: 'summary' | 'detail'
  autoRefreshEnabled: boolean
  collapsedState: Record<string, boolean>
}

const LOGS_UI_STATE_KEY = 'logs-ui-state-v1'
const AUTO_REFRESH_INTERVAL_MS = 8000
const INITIAL_FETCH_DELAY_MS = 80
const PARSE_CHUNK_SIZE = 120

const CATEGORY_COLORS: Record<string, string> = {
  SYSTEM: '#9e9e9e',
  CONFIG: '#607d8b',
  LOG: '#9e9e9e',
  AUTH: '#4caf50',
  SESSION: '#00bcd4',
  FILE: '#ff9800',
  CHAT: '#2196f3',
  API: '#8bc34a',
  CACHE: '#9c27b0',
  ACCOUNT: '#f44336',
  MULTI: '#673ab7',
}

const ACCOUNT_COLORS: Record<string, string> = {
  account_1: '#9c27b0',
  account_2: '#e91e63',
  account_3: '#00bcd4',
  account_4: '#4caf50',
  account_5: '#ff9800',
}

const LAYER_META: Record<LogLayer, { label: string; badgeClass: string }> = {
  system: { label: 'SYSTEM', badgeClass: 'rounded border border-slate-500/40 bg-slate-500/10 px-2 py-0.5 text-slate-600' },
  chat: { label: 'CHAT', badgeClass: 'rounded border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-blue-600' },
  reverse: { label: 'REVERSE', badgeClass: 'rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-emerald-600' },
  other: { label: 'OTHER', badgeClass: 'rounded border border-muted bg-muted/20 px-2 py-0.5 text-muted-foreground' },
}

const SUMMARY_KEYWORDS = [
  'start | model=',
  'start model=',
  'stream success',
  'success',
  'failed',
  'timeout',
  'error',
  'warning',
  'account=',
  'user:',
  'assistant:',
  'startup',
  'stats ready',
]

const REVERSE_TERMINAL_STAGES = new Set(['success', 'stream_success', 'failed', 'stream_failed', 'exception'])

export function useLogsPage() {
  const toast = useToast()
  const logs = ref<LogEntry[]>([])
  const parsedLogs = ref<ParsedLogEntry[]>([])
  const groupedLogs = ref<GroupedLogState>({ ungrouped: [], groups: [] })
  const stats = ref<AdminLogStats | null>(null)
  const confirmOpen = ref(false)
  const collapsedState = ref<Record<string, boolean>>({})
  const rawView = ref(true)
  const detailMode = ref<'summary' | 'detail'>('summary')
  const autoRefreshEnabled = ref(true)
  const isPageActive = ref(false)
  const lastFetchedAt = ref(0)
  const rawLogContainer = ref<HTMLDivElement | null>(null)
  const structuredLogContainer = ref<HTMLDivElement | null>(null)
  const isFetching = ref(false)

  const rawRenderLimit = 3000
  const structuredRenderLimit = 3000
  const groupLogLimit = 200
  const virtualizeUngroupedThreshold = 120
  const virtualizeGroupThreshold = 180

  let searchDebounceTimer: number | null = null
  let autoRefreshTimer: number | null = null
  let fetchLogsTimer: number | null = null
  let isRestoringUiState = false
  let fetchSequence = 0

  const filters = reactive({
    level: '',
    search: '',
    limit: 300,
  })

  const levelOptions = [
    { label: '全部', value: '' },
    { label: 'INFO', value: 'INFO' },
    { label: 'WARNING', value: 'WARNING' },
    { label: 'ERROR', value: 'ERROR' },
  ]

  const getCategoryColor = (category: string) => CATEGORY_COLORS[category] || '#757575'
  const getAccountColor = (accountId: string) => ACCOUNT_COLORS[accountId] || '#757575'
  const buildAccountStyle = (accountId: string) => ({ color: getAccountColor(accountId) })
  const detectLogLayer = (message: string, tags: string[]): LogLayer => {
    if (tags.includes('CHAT') || /(?:^|\s)(assistant|user):/i.test(message)) return 'chat'
    if (tags.includes('REVERSE') || /\[REVERSE\]/i.test(message)) return 'reverse'
    if (tags.includes('SYSTEM') || /HTTP Request:/i.test(message) || /gemini\.google\.com|push\.clients6\.google\.com|uvicorn/i.test(message)) {
      return 'system'
    }
    return 'other'
  }

  const detectStage = (message: string, layer: LogLayer) => {
    const text = message.toLowerCase()
    if (layer === 'chat') {
      if (/(?:^|\s)user:/i.test(message)) return 'user'
      if (/(?:^|\s)assistant:/i.test(message)) return 'assistant'
    }
    if (layer === 'reverse') {
      if (text.includes('start | model=')) return 'start'
      if (text.includes('stream success')) return 'stream_success'
      if (text.includes('stream failed')) return 'stream_failed'
      if (text.includes(' success')) return 'success'
      if (text.includes(' failed')) return 'failed'
      if (text.includes('exception')) return 'exception'
    }
    return ''
  }

  const extractMessageField = (message: string, key: string) => {
    const source = String(message || '')
    const sourceLower = source.toLowerCase()
    const needle = `${String(key || '').toLowerCase()}=`
    const start = sourceLower.indexOf(needle)
    if (start < 0) return ''

    let end = start + needle.length
    while (end < source.length) {
      const ch = source[end]
      if (/\s|,|\]|\)/.test(ch)) break
      end += 1
    }
    return source.slice(start + needle.length, end)
  }

  const extractFailureKind = (message: string) => {
    const match = message.match(/\(kind=([a-z0-9_:-]+)\)/i)
    return match ? String(match[1] || '').toLowerCase() : ''
  }

  const summarizeChatPreview = (text: string) => {
    const clean = String(text || '').trim()
    if (clean.length <= 96) return clean
    return `${clean.slice(0, 96)}…`
  }

  const uniq = <T,>(items: T[]) => Array.from(new Set(items))

  const buildLogTags = (log: ParsedLogEntry) => (
    [
      ...log.tags.map((tag) => ({
        key: tag,
        text: tag,
        class: 'ui-badge text-white',
        style: { backgroundColor: getCategoryColor(tag) },
      })),
      ...(log.lane
        ? [{
            key: `lane:${log.lane}`,
            text: `lane:${log.lane}`,
            class: 'rounded border border-cyan-500/40 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-600',
            style: {},
          }]
        : []),
      ...(log.kind
        ? [{
            key: `kind:${log.kind}`,
            text: `kind:${log.kind}`,
            class: 'rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-700',
            style: {},
          }]
        : []),
      ...(log.servedLabel
        ? [{
            key: `served:${log.servedLabel}`,
            text: `served:${log.servedLabel}`,
            class: 'rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-700',
            style: {},
          }]
        : []),
    ]
  )

  const levelBadgeClass = (level: LogEntry['level']) => {
    const base = 'rounded px-2 py-0.5 text-[10px] font-semibold'
    if (level === 'INFO') return `${base} bg-blue-100 text-blue-700`
    if (level === 'WARNING') return `${base} bg-amber-100 text-amber-700`
    if (level === 'ERROR' || level === 'CRITICAL') return `${base} bg-rose-100 text-rose-700`
    return `${base} bg-violet-100 text-violet-700`
  }

  const statusBadgeClass = (status: string) => {
    const base = 'rounded-md px-2 py-0.5 text-[11px] font-semibold'
    if (status === 'success') return `${base} bg-emerald-100 text-emerald-700`
    if (status === 'error') return `${base} bg-rose-100 text-rose-700`
    if (status === 'timeout') return `${base} bg-amber-100 text-amber-700`
    return `${base} bg-amber-100 text-amber-700`
  }

  const statusLabel = (status: string) => {
    if (status === 'success') return '成功'
    if (status === 'error') return '失败'
    if (status === 'timeout') return '超时'
    return '进行中'
  }

  const parseLogMessage = (message: string) => {
    let remaining = message
    const tags: string[] = []
    let accountId = ''
    const tagRegex = /^\[([A-Za-z0-9_]+)\]/

    while (true) {
      const match = remaining.match(tagRegex)
      if (!match) break
      const tag = match[1]
      remaining = remaining.slice(match[0].length).trim()

      if (tag.startsWith('req_')) continue
      if (tag.startsWith('account_')) {
        accountId = tag
        continue
      }
      tags.push(tag)
    }

    return { tags, accountId, text: remaining }
  }

  const parseLogEntry = (log: LogEntry, index = 0): ParsedLogEntry => {
    const parsed = parseLogMessage(log.message)
    const reqMatch = log.message.match(/\[req_([a-z0-9]+)\]/i)
    const rawLayer = String(log.layer || '').toLowerCase()
    const layer: LogLayer = (rawLayer === 'system' || rawLayer === 'chat' || rawLayer === 'reverse' || rawLayer === 'other')
      ? (rawLayer as LogLayer)
      : detectLogLayer(log.message, parsed.tags)
    return {
      ...log,
      rowId: String(log.row_id || `${log.time}-${log.level}-${index}`),
      tags: Array.isArray(log.tags) ? log.tags : parsed.tags,
      accountId: String(log.account_id || parsed.accountId || ''),
      text: String(log.text || parsed.text || log.message),
      reqId: String(log.req_id || (reqMatch ? reqMatch[1] : '')),
      layer,
      lane: String(log.lane || extractMessageField(log.message, 'lane') || ''),
      model: String(log.model || extractMessageField(log.message, 'model') || ''),
      kind: String(log.kind || extractFailureKind(log.message) || ''),
      stage: String(log.stage || detectStage(log.message, layer) || ''),
      servedLabel: String(log.served_label || extractMessageField(log.message, 'served_label') || ''),
    }
  }

  const parseLogTime = (value: string) => {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) return new Date(value)
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return new Date(value.replace(' ', 'T'))
    if (/^\d{2}:\d{2}:\d{2}$/.test(value)) {
      const now = new Date()
      const [hours, minutes, seconds] = value.split(':').map(Number)
      const parsed = new Date(now)
      parsed.setHours(hours, minutes, seconds, 0)
      return parsed
    }
    return null
  }

  const hasTag = (log: ParsedLogEntry, tag: string) => log.tags.includes(tag)
  const pickLastLog = (items: ParsedLogEntry[], predicate: (log: ParsedLogEntry) => boolean) => {
    for (let i = items.length - 1; i >= 0; i -= 1) {
      if (predicate(items[i])) return items[i]
    }
    return null
  }

  const selectSummaryLogs = (items: ParsedLogEntry[]) => {
    const picked = new Set<string>()
    const push = (log: ParsedLogEntry | null) => {
      if (!log) return
      picked.add(log.rowId)
    }

    push(items.find((log) => log.layer === 'reverse' && log.stage === 'start') || null)
    push(pickLastLog(items, (log) => log.layer === 'chat' && log.stage === 'user'))
    push(pickLastLog(items, (log) => log.layer === 'chat' && log.stage === 'assistant'))
    push(pickLastLog(items, (log) => log.layer === 'reverse' && REVERSE_TERMINAL_STAGES.has(log.stage)))

    items.forEach((log) => {
      if (log.level === 'ERROR' || log.level === 'CRITICAL' || log.level === 'WARNING') picked.add(log.rowId)
    })

    items.filter((log) => log.layer === 'system').slice(-2).forEach((log) => picked.add(log.rowId))
    return items.filter((log) => picked.has(log.rowId))
  }

  const isTerminalSuccessLog = (log: ParsedLogEntry) => {
    const msg = log.message.toLowerCase()
    const statusTagMatched = hasTag(log, 'REVERSE') || hasTag(log, 'IMAGE-GEN') || hasTag(log, 'IMAGE-EDIT') || hasTag(log, 'CHAT-IMAGE')
    if (!statusTagMatched) return false
    return (
      msg.includes('stream success')
      || msg.includes(' success')
      || msg.includes('成功')
      || msg.includes('响应完成')
      || msg.includes('completed')
    )
  }

  const isTerminalErrorLog = (log: ParsedLogEntry) => {
    const msg = log.message.toLowerCase()
    if (log.level === 'ERROR' || log.level === 'CRITICAL') return true
    const statusTagMatched = hasTag(log, 'REVERSE') || hasTag(log, 'IMAGE-GEN') || hasTag(log, 'IMAGE-EDIT') || hasTag(log, 'CHAT-IMAGE')
    if (!statusTagMatched) return false
    return (
      msg.includes(' failed')
      || msg.includes('failure')
      || msg.includes('error')
      || msg.includes('失败')
    )
  }

  const isTerminalTimeoutLog = (log: ParsedLogEntry) => {
    const msg = log.message.toLowerCase()
    const statusTagMatched = hasTag(log, 'REVERSE') || hasTag(log, 'IMAGE-GEN') || hasTag(log, 'IMAGE-EDIT') || hasTag(log, 'CHAT-IMAGE')
    if (!statusTagMatched) return false
    return msg.includes('timeout') || msg.includes('timed out') || msg.includes('超时')
  }

  const getGroupStatus = (groupLogs: ParsedLogEntry[]) => {
    if (!groupLogs.length) return 'in_progress'

    for (let i = groupLogs.length - 1; i >= 0; i -= 1) {
      const log = groupLogs[i]
      if (isTerminalTimeoutLog(log)) return 'timeout'
      if (isTerminalErrorLog(log)) return 'error'
      if (isTerminalSuccessLog(log)) return 'success'
    }

    const lastLog = groupLogs[groupLogs.length - 1]
    const parsedTime = parseLogTime(lastLog.time)
    if (parsedTime) {
      const diffMinutes = (Date.now() - parsedTime.getTime()) / 1000 / 60
      if (diffMinutes > 5) return 'timeout'
    }
    return 'in_progress'
  }

  const isSystemRequestTrace = (log: ParsedLogEntry) => log.layer === 'system'

  const buildGroupedLogs = (items: ParsedLogEntry[], apiGroups?: AdminLogGroup[]): GroupedLogState => {
    if (Array.isArray(apiGroups) && apiGroups.length > 0) {
      const rowMap = new Map(items.map((item) => [item.rowId, item]))
      const groupedRowIds = new Set<string>()
      const groups = apiGroups
        .map((group) => {
          const groupLogs = (group.row_ids || [])
            .map((rowId: string) => rowMap.get(rowId))
            .filter(Boolean) as ParsedLogEntry[]
          groupLogs.forEach((log) => groupedRowIds.add(log.rowId))
          if (!groupLogs.length) return null
          return {
            id: String(group.id || group.request_id || ''),
            logs: groupLogs,
            status: String(group.status || 'in_progress'),
            accountId: String(group.account_id || ''),
            model: String(group.model || ''),
            lane: String(group.lane || ''),
            terminalKind: String(group.terminal_kind || ''),
            startedAt: String(group.started_at || ''),
            endedAt: String(group.ended_at || ''),
            userPreview: String(group.user_preview || ''),
            assistantPreview: String(group.assistant_preview || ''),
          } satisfies GroupedLog
        })
        .filter(Boolean) as GroupedLog[]

      return {
        groups,
        ungrouped: items.filter((item) => !groupedRowIds.has(item.rowId)),
      }
    }

    const groups = new Map<string, ParsedLogEntry[]>()
    const groupOrder: string[] = []
    const ungrouped: ParsedLogEntry[] = []
    let activeReqId = ''

    items.forEach((log) => {
      const effectiveReqId = log.reqId || (activeReqId && isSystemRequestTrace(log) ? activeReqId : '')
      if (effectiveReqId) {
        activeReqId = effectiveReqId
        if (!groups.has(effectiveReqId)) {
          groups.set(effectiveReqId, [])
          groupOrder.push(effectiveReqId)
        }
        groups.get(effectiveReqId)?.push(log)
      } else {
        ungrouped.push(log)
      }
    })

    const groupList = groupOrder.map((id) => {
      const groupLogs = groups.get(id) || []
      const firstLog = groupLogs[0]
      const accountMatch = firstLog?.message.match(/\[(account_[^\]]+)\]/i)
      const modelMatch = firstLog?.message.match(/model=([^,\s]+)/i) || firstLog?.message.match(/Received request model=([^,\s]+)/i)
      const latestAccountLog = pickLastLog(groupLogs, (log) => Boolean(log.accountId))
      const latestModelLog = pickLastLog(groupLogs, (log) => Boolean(log.model))
      const latestLaneLog = pickLastLog(groupLogs, (log) => Boolean(log.lane))
      const latestKindLog = pickLastLog(groupLogs, (log) => Boolean(log.kind))
      const userLog = pickLastLog(groupLogs, (log) => log.layer === 'chat' && log.stage === 'user')
      const assistantLog = pickLastLog(groupLogs, (log) => log.layer === 'chat' && log.stage === 'assistant')

      return {
        id,
        logs: groupLogs,
        status: getGroupStatus(groupLogs),
        accountId: latestAccountLog?.accountId || firstLog?.accountId || (accountMatch ? accountMatch[1] : '') || extractMessageField(firstLog?.message || '', 'account'),
        model: latestModelLog?.model || (modelMatch ? modelMatch[1] : ''),
        lane: latestLaneLog?.lane || '',
        terminalKind: latestKindLog?.kind || '',
        startedAt: firstLog?.time || '',
        endedAt: groupLogs[groupLogs.length - 1]?.time || '',
        userPreview: summarizeChatPreview(String(userLog?.text || '').replace(/^user:\s*/i, '')),
        assistantPreview: summarizeChatPreview(String(assistantLog?.text || '').replace(/^assistant:\s*/i, '')),
      }
    })

    return { ungrouped, groups: groupList }
  }

  const getLogLayer = (log: ParsedLogEntry): LogLayer => log.layer

  const shouldKeepSummaryLog = (log: ParsedLogEntry) => {
    if (log.level === 'ERROR' || log.level === 'CRITICAL' || log.level === 'WARNING') return true
    const msg = log.message.toLowerCase()
    if (SUMMARY_KEYWORDS.some((keyword) => msg.includes(keyword))) return true
    if (log.tags.includes('SYSTEM') || log.tags.includes('AUTH') || log.tags.includes('CHAT') || log.tags.includes('REVERSE')) return true
    return false
  }

  const summarizeText = (text: string) => {
    const clean = String(text || '').trim()
    if (clean.length <= 180) return clean
    return `${clean.slice(0, 180)}...(summary)`
  }

  const toSummaryLog = (log: ParsedLogEntry): ParsedLogEntry => ({
    ...log,
    text: summarizeText(log.text),
    message: summarizeText(log.message),
  })

  const structuredView = computed(() => {
    const sourceUngrouped = detailMode.value === 'summary'
      ? groupedLogs.value.ungrouped.filter(shouldKeepSummaryLog).map(toSummaryLog)
      : groupedLogs.value.ungrouped

    const sourceGroups = detailMode.value === 'summary'
      ? groupedLogs.value.groups
        .map((group) => ({
          ...group,
          logs: selectSummaryLogs(group.logs.filter(shouldKeepSummaryLog)).map(toSummaryLog),
        }))
        .filter((group) => group.logs.length > 0)
      : groupedLogs.value.groups

    const renderCap = detailMode.value === 'detail' ? Number.MAX_SAFE_INTEGER : structuredRenderLimit
    const limitedUngrouped = sourceUngrouped.length > renderCap ? sourceUngrouped.slice(-renderCap) : sourceUngrouped
    const limitedGroups = sourceGroups.length > renderCap ? sourceGroups.slice(-renderCap) : sourceGroups

    return { ungrouped: limitedUngrouped, groups: limitedGroups }
  })

  const buildRawLogText = (items: ParsedLogEntry[]) => {
    const lines: string[] = []
    let currentBlock = ''
    for (const log of items) {
      const blockId = log.reqId ? `req_${log.reqId}` : 'system'
      if (blockId !== currentBlock) {
        if (lines.length > 0) lines.push('')
        lines.push(`---------- ${blockId} ----------`)
        currentBlock = blockId
      }
      lines.push(`${log.time} | ${log.level} | ${log.message}`)
    }
    return lines.join('\n')
  }

  const rawLogView = computed(() => {
    const sourceLogs = detailMode.value === 'summary'
      ? parsedLogs.value.filter(shouldKeepSummaryLog).map(toSummaryLog)
      : parsedLogs.value
    const total = sourceLogs.length
    const startIndex = total > rawRenderLimit ? total - rawRenderLimit : 0
    const slice = sourceLogs.slice(startIndex)
    return {
      text: buildRawLogText(slice),
      total,
      showing: slice.length,
      limited: total > slice.length,
    }
  })

  const isCollapsed = (requestId: string) => collapsedState.value[requestId] === true
  const toggleGroup = (requestId: string) => { collapsedState.value[requestId] = !isCollapsed(requestId) }
  const isGroupLimited = (group: GroupedLog) => detailMode.value !== 'detail' && group.logs.length > groupLogLimit
  const groupMetaTexts = (group: GroupedLog) => (
    uniq([
      group.model ? `model=${group.model}` : '',
      group.lane ? `lane=${group.lane}` : '',
      group.terminalKind ? `kind=${group.terminalKind}` : '',
      group.startedAt && group.endedAt && group.startedAt !== group.endedAt
        ? `${group.startedAt} → ${group.endedAt}`
        : (group.startedAt || ''),
    ].filter(Boolean))
  )

  const groupHintText = (group: GroupedLog) => (
    [
      group.userPreview ? `用户：${group.userPreview}` : '',
      group.assistantPreview ? `回复：${group.assistantPreview}` : '',
      isGroupLimited(group) ? `仅显示最近 ${groupLogLimit} 条` : '',
    ].filter(Boolean).join(' ｜ ')
  )

  const visibleGroupLogs = (group: GroupedLog) => {
    if (detailMode.value === 'detail') return group.logs
    if (group.logs.length <= groupLogLimit) return group.logs
    return group.logs.slice(-groupLogLimit)
  }

  const shouldVirtualizeUngrouped = computed(
    () => structuredView.value.ungrouped.length >= virtualizeUngroupedThreshold
  )

  const shouldVirtualizeLayer = (layerLogs: ParsedLogEntry[]) =>
    detailMode.value === 'detail' && layerLogs.length >= virtualizeGroupThreshold

  const getGroupLayerSections = (group: GroupedLog): GroupLayerSection[] => {
    const layerLogs = visibleGroupLogs(group)
    const buckets: Record<LogLayer, ParsedLogEntry[]> = { system: [], chat: [], reverse: [], other: [] }
    layerLogs.forEach((log) => { buckets[getLogLayer(log)].push(log) })

    const order: LogLayer[] = ['system', 'chat', 'reverse', 'other']
    return order
      .filter((layer) => buckets[layer].length > 0)
      .map((layer) => ({
        key: layer,
        label: LAYER_META[layer].label,
        badgeClass: LAYER_META[layer].badgeClass,
        logs: buckets[layer],
      }))
  }

  const clampLimit = (value: number) => {
    if (!value || Number.isNaN(value)) return 300
    return Math.min(Math.max(value, 10), 1000)
  }

  const normalizeLimit = () => { filters.limit = clampLimit(Number(filters.limit)) }

  const saveUiState = () => {
    const state: LogsUiState = {
      level: String(filters.level || ''),
      search: String(filters.search || ''),
      limit: clampLimit(Number(filters.limit)),
      rawView: !!rawView.value,
      detailMode: detailMode.value,
      autoRefreshEnabled: !!autoRefreshEnabled.value,
      collapsedState: { ...collapsedState.value },
    }
    localStorage.setItem(LOGS_UI_STATE_KEY, JSON.stringify(state))
  }

  const restoreUiState = () => {
    const raw = localStorage.getItem(LOGS_UI_STATE_KEY)
    if (!raw) return
    isRestoringUiState = true
    try {
      const parsed = JSON.parse(raw) as Partial<LogsUiState>
      const allowedLevels = new Set(levelOptions.map((option) => option.value))
      const nextLevel = String(parsed.level ?? '')
      filters.level = allowedLevels.has(nextLevel) ? nextLevel : ''
      filters.search = String(parsed.search ?? '')
      filters.limit = clampLimit(Number(parsed.limit ?? 300))
      rawView.value = Boolean(parsed.rawView)
      detailMode.value = parsed.detailMode === 'detail' ? 'detail' : 'summary'
      autoRefreshEnabled.value = parsed.autoRefreshEnabled !== false
      collapsedState.value = parsed.collapsedState && typeof parsed.collapsedState === 'object'
        ? { ...parsed.collapsedState }
        : {}
    } catch {
      filters.level = ''
      filters.search = ''
      filters.limit = 300
      rawView.value = true
      detailMode.value = 'summary'
      autoRefreshEnabled.value = true
      collapsedState.value = {}
    } finally {
      isRestoringUiState = false
    }
  }

  const scrollToBottom = () => {
    if (rawView.value && rawLogContainer.value) rawLogContainer.value.scrollTop = rawLogContainer.value.scrollHeight
    if (!rawView.value && structuredLogContainer.value) structuredLogContainer.value.scrollTop = structuredLogContainer.value.scrollHeight
  }

  const yieldToBrowser = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))

  const parseLogsInChunks = async (sourceLogs: LogEntry[], token: number) => {
    const nextParsed: ParsedLogEntry[] = []
    for (let index = 0; index < sourceLogs.length; index += 1) {
      if (token !== fetchSequence) return nextParsed
      nextParsed.push(parseLogEntry(sourceLogs[index], index))
      if ((index + 1) % PARSE_CHUNK_SIZE === 0) {
        await yieldToBrowser()
      }
    }
    return nextParsed
  }

  const scheduleInitialFetch = () => {
    if (fetchLogsTimer) window.clearTimeout(fetchLogsTimer)
    fetchLogsTimer = window.setTimeout(() => {
      fetchLogsTimer = null
      void fetchLogs()
    }, INITIAL_FETCH_DELAY_MS)
  }

  const fetchLogs = async () => {
    if (isFetching.value) return
    const token = ++fetchSequence
    isFetching.value = true
    normalizeLimit()
    try {
      const response = await logsApi.list({
        limit: filters.limit,
        level: filters.level || undefined,
        search: filters.search || undefined,
      })
      if (token !== fetchSequence) return

      logs.value = response.logs
      stats.value = response.stats
      await yieldToBrowser()

      const nextParsedLogs = await parseLogsInChunks(response.logs, token)
      if (token !== fetchSequence) return

      parsedLogs.value = nextParsedLogs
      groupedLogs.value = buildGroupedLogs(nextParsedLogs, response.groups)
      lastFetchedAt.value = Date.now()
      requestAnimationFrame(scrollToBottom)
    } catch (error: any) {
      if (token !== fetchSequence) return
      toast.error(error.message || 'Log load failed')
    } finally {
      if (token === fetchSequence) {
        isFetching.value = false
      }
    }
  }

  const exportLogs = async () => {
    try {
      const response = await logsApi.list({
        limit: 1000,
        level: filters.level || undefined,
        search: filters.search || undefined,
      })
      const blob = new Blob(
        [JSON.stringify({ exported_at: new Date().toISOString(), logs: response.logs }, null, 2)],
        { type: 'application/json' }
      )
      const blobUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = blobUrl
      anchor.download = `logs_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`
      anchor.click()
      URL.revokeObjectURL(blobUrl)
      toast.success('Export succeeded')
    } catch (error: any) {
      toast.error(error.message || 'Export failed')
    }
  }

  const clearLogs = async () => {
    confirmOpen.value = false
    try {
      await logsApi.clear()
      toast.success('Logs cleared')
      await fetchLogs()
    } catch (error: any) {
      toast.error(error.message || 'Clear failed')
    }
  }

  const toggleView = () => {
    rawView.value = !rawView.value
    requestAnimationFrame(scrollToBottom)
  }

  const toggleDetailMode = () => {
    detailMode.value = detailMode.value === 'summary' ? 'detail' : 'summary'
    requestAnimationFrame(scrollToBottom)
  }

  const clearAutoRefreshTimer = () => {
    if (!autoRefreshTimer) return
    window.clearTimeout(autoRefreshTimer)
    autoRefreshTimer = null
  }

  const scheduleAutoRefresh = () => {
    clearAutoRefreshTimer()
    if (!autoRefreshEnabled.value || document.hidden || !isPageActive.value) return
    autoRefreshTimer = window.setTimeout(async () => {
      await fetchLogs()
      scheduleAutoRefresh()
    }, AUTO_REFRESH_INTERVAL_MS)
  }

  const toggleAutoRefresh = () => {
    autoRefreshEnabled.value = !autoRefreshEnabled.value
    toast.info(autoRefreshEnabled.value ? '日志自动刷新已开启（8 秒）' : '日志自动刷新已关闭')
    if (autoRefreshEnabled.value) scheduleAutoRefresh()
    else clearAutoRefreshTimer()
  }

  const handleVisibilityChange = () => {
    if (!isPageActive.value) return
    if (document.hidden) {
      clearAutoRefreshTimer()
      return
    }
    if (autoRefreshEnabled.value) scheduleAutoRefresh()
  }

  onMounted(() => {
    restoreUiState()
    isPageActive.value = true
    scheduleInitialFetch()
    scheduleAutoRefresh()
    document.addEventListener('visibilitychange', handleVisibilityChange)
  })

  onActivated(() => {
    isPageActive.value = true
    if (!lastFetchedAt.value || Date.now() - lastFetchedAt.value > AUTO_REFRESH_INTERVAL_MS) {
      void fetchLogs()
    }
    scheduleAutoRefresh()
  })

  onDeactivated(() => {
    isPageActive.value = false
    clearAutoRefreshTimer()
  })

  watch(
    [
      () => filters.level,
      () => filters.search,
      () => filters.limit,
      () => rawView.value,
      () => detailMode.value,
      () => autoRefreshEnabled.value,
      () => collapsedState.value,
    ],
    () => {
      saveUiState()
    },
    { deep: true }
  )

  watch(
    () => filters.level,
    () => {
      if (isRestoringUiState) return
      void fetchLogs()
    }
  )

  watch(
    () => filters.limit,
    () => {
      if (isRestoringUiState) return
      normalizeLimit()
      void fetchLogs()
    }
  )

  watch(
    () => filters.search,
    () => {
      if (isRestoringUiState) return
      if (searchDebounceTimer) window.clearTimeout(searchDebounceTimer)
      searchDebounceTimer = window.setTimeout(() => {
        void fetchLogs()
      }, 260)
    }
  )

  watch(
    () => autoRefreshEnabled.value,
    () => {
      if (isRestoringUiState) return
      scheduleAutoRefresh()
    }
  )

  onBeforeUnmount(() => {
    isPageActive.value = false
    clearAutoRefreshTimer()
    if (fetchLogsTimer) {
      window.clearTimeout(fetchLogsTimer)
      fetchLogsTimer = null
    }
    if (searchDebounceTimer) {
      window.clearTimeout(searchDebounceTimer)
      searchDebounceTimer = null
    }
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })

  return {
    parsedLogs,
    stats,
    filters,
    levelOptions,
    isFetching,
    fetchLogs,
    exportLogs,
    confirmOpen,
    rawView,
    toggleView,
    detailMode,
    toggleDetailMode,
    autoRefreshEnabled,
    toggleAutoRefresh,
    rawLogContainer,
    structuredLogContainer,
    rawLogView,
    structuredView,
    shouldVirtualizeUngrouped,
    levelBadgeClass,
    getCategoryColor,
    getAccountColor,
    buildAccountStyle,
    buildLogTags,
    isGroupLimited,
    groupLogLimit,
    groupMetaTexts,
    groupHintText,
    isCollapsed,
    toggleGroup,
    statusBadgeClass,
    statusLabel,
    getGroupLayerSections,
    shouldVirtualizeLayer,
    clearLogs,
  }
}
