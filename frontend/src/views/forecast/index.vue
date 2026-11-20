<template>
  <section class="page" data-module="forecast">
    <header class="page-head">
      <div>
        <h2>预测分析管理</h2>
        <p class="page-desc">维护预测分析方案的算法选择、训练窗口和特征工程配置。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记预测方案</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出预测分析清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`run-${row.id}`" @click="openRun(row)">执行预测</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无预测分析数据，可先登记预测方案</td>
        </tr>
      </tbody>
    </table>

    <section class="run-panel" data-testid="run-panel">
      <h3>训练快照</h3>
      <p class="page-desc">每次预测按缺口结果确定训练样本日期并落快照：历史训练明细保留当时的数据，两次训练输入可分别核对。</p>
      <table class="data-table">
        <thead>
          <tr><th>run_id</th><th>方案编号</th><th>训练窗口</th><th>实测天数</th><th>估计天数</th><th>缺日列表</th><th>生成时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.run_id" :data-testid="`run-row-${run.run_id}`">
            <td>{{ run.run_id }}</td>
            <td>{{ run['方案编号'] ?? '—' }}</td>
            <td>{{ run['训练窗口']['起始'] }} 至 {{ run['训练窗口']['截止'] }}</td>
            <td>{{ run['实测天数'] }}</td>
            <td>{{ run['估计天数'] }}</td>
            <td>{{ run['缺日列表'].length ? run['缺日列表'].join('、') : '无' }}</td>
            <td>{{ run['生成时间'] }}</td>
            <td><button class="link" type="button" :data-testid="`view-${run.run_id}`" @click="selectedRun = run">查看样本</button></td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="8" class="empty-state">暂无训练快照，可先在方案行上执行预测</td>
          </tr>
        </tbody>
      </table>

      <div v-if="selectedRun" class="run-detail" data-testid="run-detail">
        <h4>{{ selectedRun.run_id }} 训练明细</h4>
        <p class="decision">窗口决策：{{ selectedRun['窗口决策'] }}</p>
        <table class="data-table">
          <thead>
            <tr><th>业务日期</th><th>取值</th><th>来源</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in selectedRun['训练样本']" :key="s['业务日期']" :data-source="s['来源']">
              <td>{{ s['业务日期'] }}</td>
              <td>{{ s['值'] }}</td>
              <td>{{ s['来源'] }}</td>
              <td>{{ s['来源'] === '估计' ? s['估计方法'] : s['观测来源'] }}</td>
            </tr>
          </tbody>
        </table>
        <h4>预测结果（{{ selectedRun['预测结果']['model'] }}）</h4>
        <table class="data-table">
          <thead>
            <tr><th>预测日期</th><th>预测值</th></tr>
          </thead>
          <tbody>
            <tr v-for="(d, i) in selectedRun['预测结果']['forecast_dates']" :key="d">
              <td>{{ d }}</td>
              <td>{{ selectedRun['预测结果']['predictions'][i] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="runTarget" class="modal-backdrop" data-testid="run-modal">
      <form class="modal" @submit.prevent="submitRun">
        <h3>执行预测 · {{ runTarget['方案编号'] }}</h3>
        <label class="create-field">
          <span>窗口起始</span>
          <input v-model="runForm.start" data-testid="run-start" placeholder="2026-08-21" required />
        </label>
        <label class="create-field">
          <span>窗口截止</span>
          <input v-model="runForm.end" data-testid="run-end" placeholder="2026-08-30" required />
        </label>
        <label class="create-field">
          <span>缺口策略</span>
          <select v-model="runForm.strategy" data-testid="run-strategy">
            <option value="估计保留">估计保留（保留早期周期，缺日估计）</option>
            <option value="截断末段">截断末段（末段连续实测足够时优先减少估计误差）</option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="runTarget = null">取消</button>
          <button class="btn primary" data-testid="run-submit" type="submit" :disabled="running">
            {{ running ? '执行中…' : '确认执行' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记预测方案</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条预测分析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface TrainingSample {
  业务日期: string
  值: number
  来源: string
  观测来源?: string
  估计方法?: string
}

interface ForecastRun {
  run_id: string
  方案编号: string | null
  预测指标: string
  预测算法: string
  训练窗口: { 起始: string; 截止: string }
  实测天数: number
  估计天数: number
  缺日列表: string[]
  窗口决策: string
  训练样本: TrainingSample[]
  预测结果: { model: string; predictions: number[]; forecast_dates: string[] }
  生成时间: string
}

const ENDPOINT = '/api/forecast'
const columns = ["方案编号", "预测指标", "预测算法", "训练窗口", "预测周期", "置信水平", "特征列表", "模型版本"]
const actions = ["启动训练", "发布模型", "废弃方案"]
const statuses = ["草稿", "训练中", "已发布", "已废弃"]
const metricNames = ["方案总数", "已发布方案", "训练中方案"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

const runTarget = ref<Row | null>(null)
const runForm = ref({ start: '2026-08-21', end: '2026-08-30', strategy: '估计保留' })
const running = ref(false)
const runs = ref<ForecastRun[]>([])
const selectedRun = ref<ForecastRun | null>(null)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'forecast.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('预测分析动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '预测分析操作失败'
  }
}

function openRun(row: Row) {
  runTarget.value = row
  errorMessage.value = ''
}

async function submitRun() {
  if (!runTarget.value) return
  running.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${runTarget.value.id}/run`, {
      method: 'POST',
      body: JSON.stringify({ 窗口起始: runForm.value.start, 窗口截止: runForm.value.end, 缺口策略: runForm.value.strategy }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '预测执行失败')
    runTarget.value = null
    selectedRun.value = payload.entry as ForecastRun
    await loadRuns()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '预测执行失败'
  } finally {
    running.value = false
  }
}

async function loadRuns() {
  try {
    const response = await request(`${ENDPOINT}/runs`)
    if (!response.ok) return
    const payload = await response.json()
    runs.value = (payload.items ?? []) as ForecastRun[]
  } catch {
    // 快照列表读取失败不阻断页面
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('预测方案列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '预测分析列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadRuns()
})
</script>

<style scoped>
.run-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.run-panel h3 { margin: 0 0 4px; }
.run-panel h4 { margin: 16px 0 8px; }
.run-detail { margin-top: 12px; }
.decision { font-size: 12px; color: var(--muted); }
tr[data-source='估计'] td { background: #fffaeb; }
</style>
