import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import type { ChangeTicket } from '@/api/types'
import ChangeImpactPanel from '@/components/ChangeImpactPanel'

const ENDPOINT = '/api/factdef'
const columns = [
  "事实编号",
  "事实名称",
  "源表名称",
  "粒度说明",
  "关联维度",
  "度量字段",
  "分区字段",
  "最近刷新"
]
const actions = [
  "提交审批",
  "发布事实",
  "废弃事实"
]
const statuses = [
  "草稿",
  "待审批",
  "已生效",
  "已废弃"
]
const stats = [
  { label: "事实定义总数", key: "total" },
  { label: "已生效事实", key: "effective" },
  { label: "待复核变更", key: "pending" },
]
const TRACKED_FIELDS = ["粒度说明", "关联维度", "度量字段", "分区字段"] as const

type Row = Record<string, string | number | null>

type ChangePayload = {
  entry: Row
  tickets: ChangeTicket[]
  pending: ChangeTicket | null
  version: Record<string, string | number | null>
}

export default function FactdefView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [noticeMessage, setNoticeMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Row | null>(null)
  const [changeData, setChangeData] = useState<ChangePayload | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('事实定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事实表管理列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const loadChanges = useCallback(async (row: Row) => {
    setSelected(row)
    setChangeData(null)
    setDraft(
      Object.fromEntries(
        TRACKED_FIELDS.map((field) => [field, String(row[field] ?? '')]),
      ),
    )
    try {
      const response = await request(`${ENDPOINT}/${row.id}/changes`)
      if (!response.ok) {
        throw new Error('变更影响读取失败')
      }
      setChangeData(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '变更影响读取失败')
    }
  }, [])

  async function detectChange() {
    if (!selected) return
    setErrorMessage('')
    setNoticeMessage('')
    try {
      const response = await request(`${ENDPOINT}/${selected.id}/changes/detect`, {
        method: 'POST',
        body: JSON.stringify({ values: draft }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload?.detail ?? '变更检测失败')
      }
      setNoticeMessage(payload.message)
      await loadChanges(selected)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '变更检测失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNoticeMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload?.message ?? '事实表管理动作未生效，请稍后重试')
      }
      setNoticeMessage(payload.message)
      await reload()
      if (selected && selected.id === row.id) {
        await loadChanges(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事实表管理操作失败')
    }
  }

  const statsValue: Record<string, number> = {
    total,
    effective: rows.filter((row) => row.status === '已生效').length,
    pending: rows.filter((row) => row.处理状态 === '待复核').length,
  }

  return (
    <section className="page" data-module="factdef">
      <header className="page-head">
        <div>
          <h2>事实表管理</h2>
          <p className="page-desc">
            维护事实表的粒度定义、维度关联与度量清单。口径变更会触发失效检测，
            新版本必须等受影响聚合重跑并核对后才对下游生效。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('事实定义登记入口尚未接入审批流')}>
            登记事实定义
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出事实表管理清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{statsValue[item.key]}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>版本状态</th>
            <th>处理状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row.版本状态 ?? '—'}</td>
              <td>{row.处理状态 ?? '正常'}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void loadChanges(row)}>
                  查看影响
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 3}>暂无事实表管理数据，可先登记事实定义</td>
            </tr>
          )}
        </tbody>
      </table>

      {(noticeMessage || errorMessage) && (
        <footer className="page-foot">
          {noticeMessage && <span className="notice-text">{noticeMessage}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </footer>
      )}

      {selected && (
        <section className="change-editor">
          <header className="impact-head">
            <h3>变更检测：{String(selected.事实编号)}</h3>
            {changeData?.pending && (
              <span className="badge badge-warn">v{changeData.pending.新版本号} 待复核，不能单独宣称已生效</span>
            )}
          </header>
          <div className="filter-bar">
            {TRACKED_FIELDS.map((field) => (
              <label key={field} className="filter-item">
                <span>{field}</span>
                <input
                  value={draft[field] ?? ''}
                  onChange={(event) => setDraft((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              </label>
            ))}
            <button className="btn primary" type="button" onClick={() => void detectChange()}>
              检测变更并登记待办
            </button>
            <button className="btn ghost" type="button" onClick={() => setSelected(null)}>收起</button>
          </div>
        </section>
      )}

      {selected && changeData && (
        <>
          {changeData.tickets.length === 0 && (
            <p className="page-desc">该事实定义暂无口径变更待办。</p>
          )}
          {changeData.tickets.map((ticket) => (
            <ChangeImpactPanel key={ticket.变更编号} ticket={ticket} />
          ))}
        </>
      )}

      {!selected && (
        <footer className="page-foot">
          <span>共 {total} 条事实表管理记录</span>
        </footer>
      )}
    </section>
  )
}
