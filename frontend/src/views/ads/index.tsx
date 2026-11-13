import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/ads'
const columns = [
  "流水编号",
  "输入表名",
  "输出表名",
  "输出格式",
  "推送目标",
  "推送周期",
  "缓存时间",
  "执行批次"
]
const actions = [
  "启动输出",
  "暂停输出",
  "归档流水"
]
const statuses = [
  "草稿",
  "运行中",
  "已暂停",
  "已完成"
]
const stats = [
  { label: "输出流水总数", key: "total" },
  { label: "待复核输出", key: "pending" },
  { label: "可正常输出", key: "ready" },
]

type Row = Record<string, string | number | null>

export default function AdsView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [noticeMessage, setNoticeMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('输出流水列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '应用层输出列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

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
        throw new Error('应用层输出动作未生效，请稍后重试')
      }
      if (payload.ok === false) {
        throw new Error(payload.message)
      }
      setNoticeMessage(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '应用层输出操作失败')
    }
  }

  const statsValue: Record<string, number> = {
    total,
    pending: rows.filter((row) => row.处理状态 === '待复核（暂停输出）').length,
    ready: rows.filter((row) => row.处理状态 === '已复核').length,
  }

  return (
    <section className="page" data-module="ads">
      <header className="page-head">
        <div>
          <h2>应用层输出管理</h2>
          <p className="page-desc">维护应用层输出流水的配置参数、推送状态与批次记录，为数据服务输出提供可验证的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('输出流水登记入口尚未接入审批流')}>
            登记输出流水
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出应用层输出清单
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
            <th>处理状态</th>
            <th>复核结论</th>
            <th>待办</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row.处理状态 === '待复核（暂停输出）' ? 'row-warn' : ''}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row.处理状态 || '正常'}</td>
              <td>{row.复核结论 || '—'}</td>
              <td>{row.待办 || '—'}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 4}>暂无应用层输出数据，可先登记输出流水</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条应用层输出记录</span>
        {noticeMessage && <span className="notice-text">{noticeMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
