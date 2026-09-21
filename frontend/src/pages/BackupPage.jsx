import React, { useState } from 'react'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import FormControl from '../components/FormControl'
import StateDialog from '../components/StateDialog'
import StatusTag from '../components/StatusTag'

const LOCAL_DATA_TYPES = [
  ['资料原文件', '保存在本机'],
  ['资料索引与切块', '保存在本机'],
  ['生成文稿与导出文件', '保存在本机'],
  ['客户端本地设置', '保存在本机'],
]

export default function BackupPage() {
  const [capabilityDialog, setCapabilityDialog] = useState(null)

  return (
    <div className="capability-page backup-page">
      <h1>本地数据与备份</h1>
      <p className="page-desc">了解本地数据范围和尚未开放的备份能力</p>

      <section className="capability-card local-data-overview">
        <div className="capability-card__header">
          <div>
            <div className="capability-card__eyebrow">当前数据状态</div>
            <h2>资料保存在当前电脑</h2>
            <p>本页只说明已有的本地保存边界，不读取或虚构数量、容量和备份时间。</p>
          </div>
          <StatusTag tone="success">本地保存</StatusTag>
        </div>
        <div className="local-data-grid">
          {LOCAL_DATA_TYPES.map(([title, status]) => (
            <div className="local-data-item" key={title}>
              <span>{title}</span>
              <strong>{status}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="capability-card">
        <div className="capability-card__header">
          <div>
            <h2>当前数据位置</h2>
            <p>具体路径由当前安装目录和本机配置决定。</p>
          </div>
        </div>
        <FormControl label="本地数据目录" description="当前前端没有读取文件系统位置的能力，因此不展示猜测路径。">
          <div className="capability-field">由本机安装与应用配置决定</div>
        </FormControl>
      </section>

      <div className="capability-grid capability-grid--two">
        <section className="capability-card backup-action-card">
          <div className="capability-card__header">
            <div>
              <h2>导出本地备份</h2>
              <p>完整备份文件的生成能力尚未实现。</p>
            </div>
            <StatusTag tone="warning">未开放</StatusTag>
          </div>
          <div className="capability-notice">
            当前不会生成备份文件，也不会显示未经验证的文件大小或完成时间。
          </div>
          <Button
            variant="secondary"
            onClick={() => setCapabilityDialog({
              title: '备份导出尚未开放',
              description: '当前不会生成备份文件，也不会显示虚构的导出进度、文件大小或完成时间。',
            })}
          >
            查看导出说明
          </Button>
        </section>

        <section className="capability-card backup-action-card">
          <div className="capability-card__header">
            <div>
              <h2>从备份恢复</h2>
              <p>恢复、历史版本和跨设备同步均尚未实现。</p>
            </div>
            <StatusTag tone="warning">未开放</StatusTag>
          </div>
          <div className="capability-notice">
            当前不会选择备份文件、覆盖数据或模拟恢复结果。
          </div>
          <Button
            variant="secondary"
            onClick={() => setCapabilityDialog({
              title: '备份恢复尚未开放',
              description: '当前不会选择备份文件、覆盖本机数据或模拟恢复成功。恢复能力接入前，本页仅说明真实能力边界。',
            })}
          >
            查看恢复说明
          </Button>
        </section>
      </div>

      <section className="capability-card backup-records-empty">
        <EmptyState
          icon="备"
          title="还没有备份记录"
          description="本地备份功能未接入，因此没有可验证的备份记录。"
          actionLabel="查看能力边界"
          onAction={() => setCapabilityDialog({
            title: '备份能力尚未开放',
            description: '当前仅确认资料保存在本机。本页没有云备份、自动同步、跨设备恢复或历史版本能力，也不会修改现有文件系统逻辑。',
          })}
        />
      </section>

      <StateDialog
        open={!!capabilityDialog}
        tone="warning"
        title={capabilityDialog?.title}
        description={capabilityDialog?.description}
        confirmText="知道了"
        onConfirm={() => setCapabilityDialog(null)}
        onCancel={() => setCapabilityDialog(null)}
      />
    </div>
  )
}
