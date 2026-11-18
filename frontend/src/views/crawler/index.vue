<template>
  <section class="page" data-module="crawler">
    <header class="page-head">
      <div><h2>爬虫引擎</h2><p class="page-desc">管理爬虫任务的调度配置、并发度和批次参数，驱动采集引擎执行。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记爬虫任务</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
      </div>
    </header>
    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <section class="sub-panel" data-testid="allocation-panel">
      <header class="page-head">
        <div>
          <h3>代理分配记录</h3>
          <p class="page-desc">爬虫引擎消费代理降级决策数据集生成的分配台账：等待任务与已恢复任务状态分离，校验结论同步自代理池，可核对是否使用过期检测节点。</p>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="alloc-reload" @click="reloadAllocations">刷新台账</button>
        </div>
      </header>
      <div v-if="allocations.length && allConsistent" class="check-banner" data-testid="alloc-check">
        核对通过：等待与恢复分配均以来源决策的检测时点为依据，未使用过期检测节点
      </div>
      <table class="data-table" data-testid="allocation-table">
        <thead>
          <tr>
            <th>分配编号</th>
            <th>关联任务</th>
            <th>来源决策</th>
            <th>分配状态</th>
            <th>校验结论</th>
            <th>检测时点</th>
            <th>血缘</th>
            <th>提示</th>
            <th>核对结论</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alloc in allocations" :key="alloc.id" :data-testid="`alloc-row-${alloc.id}`" :data-status="alloc.分配状态">
            <td>{{ alloc.分配编号 }}</td>
            <td>{{ alloc.关联任务 }}</td>
            <td>{{ alloc.来源决策 }}</td>
            <td><span class="badge" :class="`alloc-${alloc.分配状态}`">{{ alloc.分配状态 }}</span></td>
            <td>{{ alloc.校验结论 }}</td>
            <td>{{ alloc.检测时点 }}</td>
            <td>{{ alloc.血缘 }}</td>
            <td>{{ alloc.提示 }}</td>
            <td>{{ alloc.核对结论 }}</td>
            <td class="row-actions">
              <button
                v-if="alloc.分配状态 === '等待中'"
                class="link"
                type="button"
                :data-testid="`alloc-fail-${alloc.id}`"
                @click="markFailed(alloc)"
              >标记失败</button>
              <button
                v-if="alloc.分配状态 === '已失败'"
                class="link"
                type="button"
                :data-testid="`alloc-replay-${alloc.id}`"
                @click="replay(alloc)"
              >请求重放</button>
            </td>
          </tr>
          <tr v-if="!allocations.length">
            <td colspan="10" class="empty-state" data-testid="allocation-empty">暂无代理分配记录</td>
          </tr>
        </tbody>
      </table>
      <p v-if="allocMessage" class="deg-message" data-testid="alloc-message">{{ allocMessage }}</p>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记爬虫任务</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/crawler'
const columns = ["任务编号", "任务名称", "关联站点", "关联规则", "并发线程", "调度周期", "超时秒数", "下发批次"]
const actions = ["提交校验", "启用配置", "停用配置"]
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])

// 代理分配记录台账（消费代理降级决策数据集）
const allocations = ref<any[]>([])
const allocMessage = ref('')
const allConsistent = computed(() => allocations.value.every((alloc) => alloc.核对结论 === '一致'))

function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); allItems.value = data.items || []; applyFilter() } catch(e) { errorMessage.value = '加载失败' }
  await reloadAllocations()
}
function applyFilter() {
  let items = allItems.value
  if(dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if(dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
async function reloadAllocations() {
  allocMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/allocations`)
    if (!res.ok) throw new Error('代理分配记录读取失败')
    const data = await res.json()
    allocations.value = data.items ?? []
  } catch (e) {
    allocMessage.value = e instanceof Error ? e.message : '代理分配记录读取失败'
  }
}
async function markFailed(alloc: any) {
  allocMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/allocations/${alloc.id}/actions`, { method: 'POST', body: JSON.stringify({ action: '标记失败' }) })
    const payload = await res.json()
    allocMessage.value = payload.message || ''
    await reloadAllocations()
  } catch (e) { allocMessage.value = e instanceof Error ? e.message : '标记失败未生效' }
}
async function replay(alloc: any) {
  allocMessage.value = ''
  const remark = window.prompt('已失败的请求不能无提示地重放，请填写重放原因：') ?? ''
  try {
    const res = await request(`${ENDPOINT}/allocations/${alloc.id}/actions`, { method: 'POST', body: JSON.stringify({ action: '请求重放', remark }) })
    const payload = await res.json()
    allocMessage.value = payload.message || ''
    await reloadAllocations()
  } catch (e) { allocMessage.value = e instanceof Error ? e.message : '重放未生效' }
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }
onMounted(reload)
</script>
