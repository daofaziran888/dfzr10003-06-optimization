import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import type { ChangeTicket } from '@/api/types'
import ChangeImpactPanel from '@/components/ChangeImpactPanel'

const ENDPOINT = '/api/dws'
const columns = [
  "流水编号",
  "输入表名",
  "输出表名",
  "聚合字段",
  "汇总粒度",
  "时间窗口",
  "预计算开关",
  "执行批次"
]
const actions = [
  "启动聚合",
  "暂停聚合",
  "归档流水"
]
const reviewActions = [
  "重跑聚合",
  "核对聚合"
]
const statuses = [
  "草稿",
  "运行中",
  "已暂停",
  "已完成"
]
const stats = [
  { label: "聚合流水总数", key: "total" },
  { label: "受变更影响聚合", key: "affected" },
  { label: "待复核聚合", key: "pending" },
]

type Row = Record<string, string | number | null>

export default function DwsView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [noticeMessage, setNoticeMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [review, setReview] = useState<{ tickets: ChangeTicket[] } | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('聚合流水列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '汇总层聚合列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const loadReview = useCallback(async (row: Row | null) => {
    if (row === null) {
      setReview(null)
      setSelectedId(null)
      return
    }
    setSelectedId(Number(row.id))
    try {
      const response = await request(`${ENDPOINT}/${row.id}/review`)
      if (!response.ok) {
        throw new Error('复核视图读取失败')
      }
      setReview(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '复核视图读取失败')
    }
  }, [])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNoticeMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error('汇总层聚合动作未生效，请稍后重试')
      }
      if (payload.ok === false) {
        throw new Error(payload.message)
      }
      setNoticeMessage(payload.message)
      await reload()
      if (selectedId === Number(row.id)) {
        await loadReview(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '汇总层聚合操作失败')
    }
  }

  const statsValue: Record<string, number> = {
    total,
    affected: rows.filter((row) => Boolean(row.影响原因) || Boolean(row.复核状态)).length,
    pending: rows.filter((row) => row.复核状态 === '待复核' || row.复核状态 === '待核对').length,
  }

  return (
    <section className="page" data-module="dws">
      <header className="page-head">
        <div>
          <h2>汇总层聚合管理</h2>
          <p className="page-desc">维护汇总层聚合流水的配置参数、执行状态与批次记录，为指标汇总提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('聚合流水登记入口尚未接入审批流')}>
            登记聚合流水
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出汇总层聚合清单
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
            <th>事实版本</th>
            <th>复核状态</th>
            <th>影响窗口</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row.复核状态 && row.复核状态 !== '已核对' ? 'row-warn' : ''}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row.事实编号 ? `${row.事实编号} · v${row.事实版本}` : '—'}</td>
              <td>{row.复核状态 || '—'}</td>
              <td>{row.影响窗口 || '—'}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void loadReview(row)}>
                  查看影响
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 4}>暂无汇总层聚合数据，可先登记聚合流水</td>
            </tr>
          )}
        </tbody>
      </table>

      {selectedId !== null && review && (
        <section className="review-panel">
          <header className="impact-head">
            <h3>受影响聚合复核</h3>
            <button className="link" type="button" onClick={() => void loadReview(null)}>收起</button>
          </header>
          {review.tickets.length === 0 && (
            <p className="page-desc">该聚合流水当前没有事实定义变更待复核。</p>
          )}
          {review.tickets.map((ticket) => (
            <ChangeImpactPanel key={ticket.变更编号} ticket={ticket} title="汇总层影响视图" />
          ))}
          <div className="row-actions">
            {reviewActions.map((action) => (
              <button
                key={action}
                className="btn"
                type="button"
                onClick={() => {
                  const row = rows.find((item) => Number(item.id) === selectedId)
                  if (row) void runAction(action, row)
                }}
              >
                {action}
              </button>
            ))}
          </div>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条汇总层聚合记录</span>
        {noticeMessage && <span className="notice-text">{noticeMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
