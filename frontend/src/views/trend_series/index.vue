<template>
  <section class="page" data-module="trend_series">
    <header class="page-head">
      <div>
        <h2>时序分析管理</h2>
        <p class="page-desc">维护时序数据序列的观测配置、缺失填充、异常检测和版本追溯。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记时序序列</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出时序分析清单</button>
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
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无时序分析数据，可先登记时序序列</td>
        </tr>
      </tbody>
    </table>

    <section class="gap-panel" data-testid="gap-panel">
      <h3>时序缺口校验</h3>
      <p class="page-desc">按业务日期校验指标时序数据：一次查询只按指定范围确认缺日，校验结果写入时序缺口数据集，补数前后都能核对观测。</p>
      <form class="analysis-form" @submit.prevent="checkGaps">
        <label>
          <span>序列编号</span>
          <select v-model="gapForm.series" data-testid="gap-series">
            <option v-for="row in rows" :key="String(row.id)" :value="String(row['序列编号'])">{{ row['序列编号'] }}</option>
          </select>
        </label>
        <label>
          <span>起始日期</span>
          <input v-model="gapForm.start" data-testid="gap-start" placeholder="2026-08-21" />
        </label>
        <label>
          <span>截止日期</span>
          <input v-model="gapForm.end" data-testid="gap-end" placeholder="2026-08-30" />
        </label>
        <button class="btn primary" data-testid="gap-check" type="submit" :disabled="checking">
          {{ checking ? '校验中…' : '校验缺口' }}
        </button>
      </form>

      <div v-if="gapResult" class="gap-result" data-testid="gap-result">
        <p>
          校验编号 <strong>{{ gapResult['校验编号'] }}</strong>：范围 {{ gapResult['校验范围起始'] }} 至
          {{ gapResult['校验范围截止'] }}，应有 {{ gapResult['应有天数'] }} 天，实测 {{ gapResult['实测天数'] }} 天，
          缺日 {{ gapResult['缺日数'] }} 天
          <template v-if="gapResult['缺日列表'].length">（{{ gapResult['缺日列表'].join('、') }}）</template>
        </p>
        <table class="data-table">
          <thead>
            <tr><th>业务日期</th><th>状态</th><th>来源</th><th>GMV</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in gapResult['核对明细']" :key="d['业务日期']" :data-status="d['状态']">
              <td>{{ d['业务日期'] }}</td>
              <td>{{ d['状态'] }}</td>
              <td>{{ d['来源'] }}</td>
              <td>{{ d['gmv'] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <form class="analysis-form" @submit.prevent="submitBackfill">
        <span class="form-title">补数登记（缺日不能填实测零，同一营业日重传仍只有一条确认观测）</span>
        <label>
          <span>业务日期</span>
          <input v-model="backfillForm.day" data-testid="backfill-day" placeholder="2026-08-24" />
        </label>
        <label>
          <span>实测GMV</span>
          <input v-model="backfillForm.gmv" data-testid="backfill-gmv" placeholder="198000" />
        </label>
        <button class="btn" data-testid="backfill-submit" type="submit" :disabled="backfilling">
          {{ backfilling ? '登记中…' : '登记补数' }}
        </button>
        <span v-if="backfillMessage" class="info-text" data-testid="backfill-message">{{ backfillMessage }}</span>
      </form>

      <div v-if="gapChecks.length" class="gap-history" data-testid="gap-history">
        <h4>时序缺口数据集</h4>
        <table class="data-table">
          <thead>
            <tr><th>校验编号</th><th>校验范围</th><th>缺日数</th><th>缺日列表</th><th>校验时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in gapChecks" :key="c['校验编号']">
              <td>{{ c['校验编号'] }}</td>
              <td>{{ c['校验范围起始'] }} 至 {{ c['校验范围截止'] }}</td>
              <td>{{ c['缺日数'] }}</td>
              <td>{{ c['缺日列表'].length ? c['缺日列表'].join('、') : '无' }}</td>
              <td>{{ c['校验时间'] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记时序序列</h3>
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
      <span>共 {{ total }} 条时序分析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface GapDetail {
  业务日期: string
  状态: string
  来源: string
  gmv: number | null
}

interface GapCheck {
  id: number
  校验编号: string
  观测指标: string
  校验时间: string
  序列编号: string
  校验范围起始: string
  校验范围截止: string
  应有天数: number
  实测天数: number
  缺日数: number
  缺日列表: string[]
  核对明细: GapDetail[]
}

const ENDPOINT = '/api/trend_series'
const columns = ["序列编号", "观测指标", "时间粒度", "起始时间", "截止时间", "填充策略", "异常检测", "序列版本"]
const actions = ["初始化序列", "标记完整", "标记缺口"]
const statuses = ["待初始化", "采集中", "完整", "有缺口"]
const metricNames = ["序列总数", "完整序列", "有缺口序列"]

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

const gapForm = ref({ series: 'TREN-GMV', start: '2026-08-21', end: '2026-08-30' })
const gapResult = ref<GapCheck | null>(null)
const gapChecks = ref<GapCheck[]>([])
const checking = ref(false)
const backfillForm = ref({ day: '2026-08-24', gmv: '' })
const backfillMessage = ref('')
const backfilling = ref(false)

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
    link.download = 'trend_series.csv'
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
      throw new Error('时序分析动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '时序分析操作失败'
  }
}

async function checkGaps() {
  checking.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/gaps/check`, {
      method: 'POST',
      body: JSON.stringify({ 序列编号: gapForm.value.series, 起始日期: gapForm.value.start, 截止日期: gapForm.value.end }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '缺口校验失败')
    gapResult.value = payload.entry as GapCheck
    await loadGapChecks()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺口校验失败'
  } finally {
    checking.value = false
  }
}

async function loadGapChecks() {
  try {
    const query = new URLSearchParams({ 序列编号: gapForm.value.series }).toString()
    const response = await request(`${ENDPOINT}/gaps?${query}`)
    if (!response.ok) return
    const payload = await response.json()
    gapChecks.value = (payload.items ?? []) as GapCheck[]
  } catch {
    // 缺口数据集读取失败不阻断页面
  }
}

async function submitBackfill() {
  backfilling.value = true
  backfillMessage.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/gaps/backfill`, {
      method: 'POST',
      body: JSON.stringify({ 序列编号: gapForm.value.series, 业务日期: backfillForm.value.day, gmv: backfillForm.value.gmv }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '补数登记失败')
    backfillMessage.value = `${payload.message}（确认观测数：${payload.entry['确认观测数']}）`
    await checkGaps()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补数登记失败'
  } finally {
    backfilling.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('时序序列列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '时序分析列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadGapChecks()
})
</script>

<style scoped>
.gap-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.gap-panel h3 { margin: 0 0 4px; }
.gap-panel h4 { margin: 16px 0 8px; }
.gap-result { margin: 12px 0; }
.form-title { width: 100%; font-size: 12px; color: var(--muted); }
.info-text { color: #15803d; font-size: 12px; align-self: center; }
tr[data-status='缺失'] td { background: #fef3f2; }
</style>
