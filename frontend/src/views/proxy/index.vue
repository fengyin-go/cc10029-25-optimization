<template>
  <section class="page" data-module="proxy">
    <header class="page-head">
      <div>
        <h2>代理池管理</h2>
        <p class="page-desc">管理代理 IP 的来源、存活检测和区域分发，为采集请求提供透明的代理切换。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记代理节点</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出代理池清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无代理池数据，可先登记代理节点</td>
        </tr>
      </tbody>
    </table>

    <section class="sub-panel" data-testid="degradation-panel">
      <header class="page-head">
        <div>
          <h3>代理降级决策数据集</h3>
          <p class="page-desc">读取空池与检测失败生成降级决策：任务进入可控等待或指定替代区域；重复送交只写入一份，落库失败同一事务回退。</p>
        </div>
      </header>

      <div v-if="activeDecision" class="degradation-banner" data-testid="degradation-banner">
        空池提示：{{ activeDecision.空池原因 }} · 最近检测批次 {{ activeDecision.最近检测批次 }} · 检测时点 {{ activeDecision.检测时点 }}
        · 策略：{{ activeDecision.降级策略 }}<template v-if="activeDecision.替代区域 && activeDecision.替代区域 !== '—'">（{{ activeDecision.替代区域 }}）</template>
      </div>

      <form class="analysis-form" @submit.prevent="submitDetection">
        <label>
          <span>批次号（留空自动生成）</span>
          <input v-model="detectionBatch" data-testid="deg-batch" placeholder="CHK-0001" />
        </label>
        <label>
          <span>数据版本</span>
          <select v-model="detectionVersion" data-testid="deg-version">
            <option>冻结版</option>
            <option>最新版</option>
          </select>
        </label>
        <label v-for="row in rows" :key="String(row.id)">
          <span>{{ row.节点编号 }} 检测结论</span>
          <select v-model="detectionResults[String(row.节点编号)]" :data-testid="`deg-node-${row.节点编号}`">
            <option>未检测</option>
            <option>存活</option>
            <option>失效</option>
          </select>
        </label>
        <label>
          <span>模拟落库失败</span>
          <input v-model="simulateFail" type="checkbox" data-testid="deg-fail" class="deg-checkbox" />
        </label>
        <button class="btn primary" type="submit" data-testid="deg-submit">提交检测并生成降级决策</button>
        <button class="btn" type="button" data-testid="deg-all-dead" @click="markAll('失效')">全部置为失效</button>
        <button class="btn" type="button" data-testid="deg-all-alive" @click="markAll('存活')">全部置为存活</button>
      </form>

      <table class="data-table" data-testid="degradation-table">
        <thead>
          <tr>
            <th>决策编号</th>
            <th>空池原因</th>
            <th>最近检测批次</th>
            <th>检测时点</th>
            <th>降级策略</th>
            <th>替代区域</th>
            <th>影响任务数</th>
            <th>校验结论</th>
            <th>数据版本</th>
            <th>血缘</th>
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="dec in decisions" :key="String(dec.id)" :data-testid="`deg-row-${dec.id}`" :data-status="String(dec.status)">
            <td>{{ dec.决策编号 }}</td>
            <td>{{ dec.空池原因 }}</td>
            <td>{{ dec.最近检测批次 }}</td>
            <td>{{ dec.检测时点 }}</td>
            <td>{{ dec.降级策略 }}</td>
            <td>{{ dec.替代区域 }}</td>
            <td>{{ dec.影响任务数 }}</td>
            <td>{{ dec.校验结论 }}</td>
            <td>{{ dec.数据版本 }}</td>
            <td>{{ dec.血缘 }}</td>
            <td><span class="badge" :class="`decision-${dec.status}`">{{ dec.status }}</span></td>
            <td class="row-actions">
              <button
                v-if="dec.status === '生效中'"
                class="link"
                type="button"
                :data-testid="`deg-recover-${dec.id}`"
                @click="recoverDecision(dec)"
              >确认恢复</button>
            </td>
          </tr>
          <tr v-if="!decisions.length">
            <td colspan="12" class="empty-state" data-testid="degradation-empty">暂无数据可重试</td>
          </tr>
        </tbody>
      </table>
      <p v-if="degMessage" class="deg-message" data-testid="deg-message">{{ degMessage }}</p>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记代理节点</h3>
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
      <span>共 {{ total }} 条代理池记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/proxy'
const columns = ["节点编号", "IP地址", "端口号", "协议类型", "匿程度", "响应延迟", "存活检测", "所属区域"]
const actions = ["提交校验", "启用配置", "停用配置"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const metricNames = ["代理节点总数", "待校验代理节点", "已启用代理节点"]

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

// 代理降级决策数据集
const decisions = ref<Row[]>([])
const degMessage = ref('')
const detectionBatch = ref('')
const detectionVersion = ref('冻结版')
const simulateFail = ref(false)
const detectionResults = ref<Record<string, string>>({})
const activeDecision = computed(() => decisions.value.find((dec) => dec.status === '生效中'))

function markAll(conclusion: string) {
  for (const row of rows.value) {
    detectionResults.value[String(row.节点编号)] = conclusion
  }
}

async function submitDetection() {
  degMessage.value = ''
  try {
    const results = rows.value.map((row) => ({
      节点编号: row.节点编号,
      检测结论: detectionResults.value[String(row.节点编号)] ?? '未检测',
    }))
    const response = await request(`${ENDPOINT}/degradation`, {
      method: 'POST',
      body: JSON.stringify({
        批次号: detectionBatch.value,
        数据版本: detectionVersion.value,
        模拟落库失败: simulateFail.value,
        结果: results,
      }),
    })
    const payload = await response.json()
    degMessage.value = payload.message || ''
    if (!response.ok || !payload.ok) return
    detectionBatch.value = ''
    await Promise.all([reload(), reloadDecisions()])
  } catch (error) {
    degMessage.value = error instanceof Error ? error.message : '检测提交失败'
  }
}

async function recoverDecision(dec: Row) {
  degMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/degradation/${dec.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action: '确认恢复' }),
    })
    const payload = await response.json()
    degMessage.value = payload.message || ''
    await reloadDecisions()
  } catch (error) {
    degMessage.value = error instanceof Error ? error.message : '确认恢复失败'
  }
}

async function reloadDecisions() {
  try {
    const response = await request(`${ENDPOINT}/degradation`)
    if (!response.ok) throw new Error('代理降级决策数据集读取失败')
    const payload = await response.json()
    decisions.value = payload.items ?? []
  } catch (error) {
    degMessage.value = error instanceof Error ? error.message : '代理降级决策数据集读取失败'
  }
}

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
    link.download = 'proxy.csv'
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
      throw new Error('代理池动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '代理池操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('代理节点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    for (const row of rows.value) {
      const key = String(row.节点编号)
      if (!(key in detectionResults.value)) detectionResults.value[key] = '未检测'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '代理池列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadDecisions()
})
</script>
