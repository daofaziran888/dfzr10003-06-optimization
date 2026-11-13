/** 事实定义变更的影响视图类型：事实页与汇总页共用，保证两页展示同一份口径。 */
export type ChangeDiff = {
  字段: string
  旧值: string
  新值: string
}

export type AffectedAggregation = {
  聚合ID: number
  流水编号: string
  输入表名: string
  输出表名: string
  时间窗口: string
  原口径版本: number
  复核状态: string
  复核结论: string
  新聚合流水: string
  重跑版本: number
}

export type ChangeTicket = {
  变更编号: string
  事实定义ID: number
  事实编号: string
  事实名称: string
  源表名称: string
  变更字段: string[]
  字段差异: ChangeDiff[]
  旧版本号: number
  新版本号: number
  生效时间: string
  影响原因: string
  旧口径: Record<string, string>
  新口径: Record<string, string>
  影响窗口: string[]
  处理状态: string
  复核状态: string
  复核结论: string
  版本状态: string
  受影响聚合: AffectedAggregation[]
  合并次数: number
  裁决说明: string
  创建时间: string
  更新时间: string
  复核时间: string
}

export const TRACKED_FIELDS = ['粒度说明', '关联维度', '度量字段', '分区字段'] as const
export const FACT_STATUS_LABELS = ['待复核', '已复核']
