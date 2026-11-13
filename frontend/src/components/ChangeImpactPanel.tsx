import type { ChangeTicket } from '@/api/types'

type Props = {
  ticket: ChangeTicket
  /** 同一变更在事实页与汇总页都渲染这份视图，避免两页口径不一致。 */
  title?: string
  compact?: boolean
}

export default function ChangeImpactPanel({ ticket, title = '事实定义变更影响', compact }: Props) {
  const pending = ticket.处理状态 !== '已复核'
  return (
    <section className="impact-panel" data-change={ticket.变更编号}>
      <header className="impact-head">
        <h3>
          {title}：{ticket.变更编号} · {ticket.事实编号}
        </h3>
        <span className={`badge ${pending ? 'badge-warn' : 'badge-ok'}`}>
          {ticket.处理状态 || '待复核'}
        </span>
      </header>
      <dl className="impact-meta">
        <div>
          <dt>影响原因</dt>
          <dd>{ticket.影响原因 || '—'}</dd>
        </div>
        <div>
          <dt>影响窗口</dt>
          <dd>{ticket.影响窗口.length ? ticket.影响窗口.join('、') : '—'}</dd>
        </div>
        <div>
          <dt>版本</dt>
          <dd>
            v{ticket.旧版本号} → v{ticket.新版本号}（生效时间 {ticket.生效时间 || '—'}）
          </dd>
        </div>
        <div>
          <dt>版本状态</dt>
          <dd>{ticket.版本状态 || '—'}</dd>
        </div>
        <div>
          <dt>复核结论</dt>
          <dd>{ticket.复核结论 || (pending ? '尚未完成复核，新版本不对下游生效' : '—')}</dd>
        </div>
        {!compact && ticket.裁决说明 && (
          <div>
            <dt>优先级裁决</dt>
            <dd>{ticket.裁决说明}</dd>
          </div>
        )}
      </dl>

      <div className="impact-columns">
        <div>
          <h4>旧口径（历史聚合按此保留）</h4>
          <ul>
            {Object.entries(ticket.旧口径).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <strong>{value || '—'}</strong>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4>新口径（复核通过后生效）</h4>
          <ul>
            {Object.entries(ticket.新口径).map(([key, value]) => {
              const changed = ticket.字段差异.some((item) => item.字段 === key)
              return (
                <li key={key} className={changed ? 'changed' : ''}>
                  <span>{key}</span>
                  <strong>{value || '—'}</strong>
                </li>
              )
            })}
          </ul>
        </div>
      </div>

      <table className="data-table impact-table">
        <thead>
          <tr>
            <th>受影响聚合</th>
            <th>输入 → 输出</th>
            <th>时间窗口</th>
            <th>原口径版本</th>
            <th>复核状态</th>
            <th>新聚合流水</th>
          </tr>
        </thead>
        <tbody>
          {ticket.受影响聚合.map((entry) => (
            <tr key={entry.聚合ID}>
              <td>{entry.流水编号}</td>
              <td>
                {entry.输入表名} → {entry.输出表名}
              </td>
              <td>{entry.时间窗口 || '—'}</td>
              <td>v{entry.原口径版本}</td>
              <td>{entry.复核状态 || '待复核'}</td>
              <td>{entry.新聚合流水 || '—'}</td>
            </tr>
          ))}
          {ticket.受影响聚合.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={6}>
                当前事实定义没有引用它的聚合流水
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
