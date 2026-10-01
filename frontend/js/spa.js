/**
 * Интерфейс на Vue 3
 */
import { createApp, reactive, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';
import { createMapView } from './map-view.js';

async function request(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch('/api' + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error('Сервер недоступен. Запустите start.bat.');
  }
  if (response.status === 401) {
    if (router.currentRoute.value.name !== 'login') router.push({ name: 'login' });
    throw new Error('Нужно войти в систему');
  }
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === 'string') detail = payload.detail;
    } catch { /* не JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

const session = reactive({ me: null, error: '' });

function homeOf(role) {
  return { admin: 'admin', employee: 'employee', aircraft: 'aircraft' }[role] || 'login';
}

const ImpersonationBar = {
  setup() {
    async function back() {
      session.me = (await request('/auth/stop-impersonation', { method: 'POST' })).me;
      router.push({ name: 'admin' });
    }
    return { session, back };
  },
  template: `
    <div class="impersonation" v-if="session.me?.impersonating">
      <span>Режим демонстрации: вы смотрите систему как <b>{{ session.me.display_name }}</b></span>
      <button class="btn btn-outline btn-small" type="button" @click="back">Вернуться в админку</button>
    </div>
  `,
};

const TopBar = {
  props: { subtitle: String },
  components: { ImpersonationBar },
  setup() {
    async function logout() {
      await request('/auth/logout', { method: 'POST' });
      session.me = null;
      router.push({ name: 'login' });
    }
    return { session, logout };
  },
  template: `
    <div>
      <ImpersonationBar />
      <header class="topbar">
        <div class="brand">
          <span class="logo">✈</span>
          <div>
            <div class="title">АСУ ОТО «Перрон»</div>
            <div class="subtitle">{{ subtitle }}</div>
          </div>
        </div>
        <div class="topbar-right">
          <div class="clock">
            <div class="clock-value">{{ session.me?.display_name }}</div>
            <div class="clock-label">{{ session.me?.role }}</div>
          </div>
          <button class="btn btn-ghost" type="button" @click="logout">Выйти</button>
        </div>
      </header>
    </div>
  `,
};

const LoginView = {
  setup() {
    const username = ref('admin');
    const password = ref('admin');
    const error = ref('');
    const directory = ref([]);

    onMounted(async () => {
      try {
        directory.value = (await request('/auth/directory')).accounts;
      } catch { /* страница входа должна открываться и без этого */ }
    });

    async function submit() {
      error.value = '';
      try {
        const result = await request('/auth/login', {
          method: 'POST',
          body: { username: username.value, password: password.value },
        });
        session.me = result.me;
        router.push({ name: homeOf(result.me.role) });
      } catch (err) {
        error.value = err.message;
      }
    }

    function pick(account) {
      username.value = account.username;
      password.value = account.hint;
    }

    return { username, password, error, directory, submit, pick };
  },
  template: `
    <div class="login-page">
      <form class="login-card" @submit.prevent="submit">
        <h1>АСУ ОТО «Перрон»</h1>
        <p class="lead">Вход по роли: диспетчер, инженер или борт.</p>
        <div class="login-error" v-if="error">{{ error }}</div>
        <label class="field"><span>Логин</span>
          <input v-model="username" autocomplete="username">
        </label>
        <label class="field"><span>Пароль</span>
          <input v-model="password" type="password" autocomplete="current-password">
        </label>
        <button class="btn btn-primary" type="submit">Войти</button>
        <div class="account-hint">
          Администратор: <b>admin / admin</b>. Остальные аккаунты: пароль <b>demo</b>.
          <div class="chip-row">
            <button class="chip" type="button" v-for="item in directory" :key="item.id" @click="pick(item)">
              {{ item.username }}
            </button>
          </div>
        </div>
      </form>
    </div>
  `,
};

const AircraftView = {
  components: { TopBar },
  setup() {
    const data = ref(null);
    const faultId = ref('');
    const error = ref('');
    const sending = ref(false);

    async function load() {
      data.value = await request('/me/aircraft');
      if (!faultId.value && data.value.fault_types.length) {
        faultId.value = data.value.fault_types[0].id;
      }
    }

    async function send() {
      sending.value = true;
      error.value = '';
      try {
        data.value = await request('/me/aircraft/requests', {
          method: 'POST',
          body: { fault_type_id: faultId.value },
        });
      } catch (err) {
        error.value = err.message;
      } finally {
        sending.value = false;
      }
    }

    async function cancel() {
      error.value = '';
      try {
        data.value = await request('/me/aircraft/requests/cancel', { method: 'POST' });
      } catch (err) {
        error.value = err.message;
      }
    }

    onMounted(load);
    const timer = setInterval(() => { if (!sending.value) load().catch(() => {}); }, 4000);
    onBeforeUnmount(() => clearInterval(timer));

    return { data, faultId, error, sending, send, cancel };
  },
  template: `
    <div class="role-shell">
      <TopBar subtitle="Кабина воздушного судна" />
      <main class="role-main" v-if="data">
        <section class="hero-card">
          <div class="board">{{ data.aircraft.id }}</div>
          <div class="meta">
            {{ data.aircraft.type_name }} · {{ data.aircraft.stand_name }}
            <span v-if="data.aircraft.flight_number"> · рейс {{ data.aircraft.flight_number }}</span>
          </div>
          <div v-if="data.request" class="call-card">
            <h3>Заявка {{ data.request.id }}</h3>
            <p>{{ data.request.fault_name }}</p>
            <p class="quiet">{{ data.request.status_name }}
              <span v-if="data.request.employee_name"> · {{ data.request.employee_name }}</span>
              <span v-if="data.request.eta"> · прибытие {{ data.request.eta }}</span>
            </p>
            <button class="btn btn-outline btn-small" type="button" @click="cancel">Отменить заявку</button>
          </div>
          <template v-else>
            <p class="card-hint">Сообщите о неисправности — диспетчерская система подберёт инженера.</p>
            <div class="login-error" v-if="error">{{ error }}</div>
            <label class="field"><span>Что произошло</span>
              <select v-model="faultId">
                <option v-for="item in data.fault_types" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
            </label>
            <button class="btn btn-primary" type="button" :disabled="sending" @click="send">
              Отправить заявку
            </button>
          </template>
        </section>
      </main>
    </div>
  `,
};

const EmployeeView = {
  components: { TopBar },
  setup() {
    const data = ref(null);
    const svgEl = ref(null);
    let map = null;
    let reference = null;

    const tip = ref('');

    async function draw() {
      if (!svgEl.value || !data.value || !reference) return;
      if (map) map.destroy();
      map = createMapView(svgEl.value, reference, {
        onEmployeeHover: (employee) => { tip.value = employee.tooltip; },
        onEmployeeLeave: () => { tip.value = ''; },
      });
      map.render({
        employees: data.value.employees,
        aircraft: data.value.aircraft,
        vehicles: data.value.vehicles,
        closed_edge_ids: data.value.closed_edge_ids,
      }, {
        highlightEmployeeId: data.value.employee.id,
        targetStandNodeId: data.value.call?.stand_node_id,
      });
      if (data.value.route) map.drawRoute(data.value.route, { standNodeId: data.value.call?.stand_node_id });
      else map.clearRoute();
    }

    async function load() {
      data.value = await request('/me/employee');
      if (!reference) reference = await request('/reference');
      await nextTick();
      draw();
    }

    async function finish() {
      data.value = await request('/me/employee/complete', { method: 'POST' });
      await nextTick();
      draw();
    }

    async function cancelCall() {
      data.value = await request('/me/employee/cancel', { method: 'POST' });
      await nextTick();
      draw();
    }

    onMounted(load);
    const timer = setInterval(() => load().catch(() => {}), 4000);
    onBeforeUnmount(() => {
      clearInterval(timer);
      if (map) map.destroy();
    });

    return { data, svgEl, tip, finish, cancelCall };
  },
  template: `
    <div class="role-shell">
      <TopBar subtitle="Кабинет инженера" />
      <main class="role-main wide employee-layout" v-if="data">
        <section class="card">
          <h2>{{ data.employee.name }}</h2>
          <p>
            <span class="status-pill" :class="{ busy: data.employee.busy_label !== 'Свободен' }">
              {{ data.employee.busy_label }}
            </span>
          </p>
          <p class="quiet">{{ data.employee.qualification_text }} · допуски {{ data.employee.type_ratings.join(', ') }}</p>
          <div class="call-card" v-if="data.call">
            <h3>{{ data.call.fault_name }}</h3>
            <p>{{ data.call.aircraft_id }} · {{ data.call.stand_name }}</p>
            <p class="quiet">Выход {{ data.call.departure }} · прибытие {{ data.call.arrival }} · {{ data.call.mode_text }}</p>
            <p v-if="data.call.notification">{{ data.call.notification.body }}</p>
            <div class="actions" style="margin-top:10px">
              <button class="btn btn-ok btn-small" type="button" @click="finish">Завершить работу</button>
              <button class="btn btn-outline btn-small" type="button" @click="cancelCall">Отменить заявку</button>
            </div>
          </div>
          <p class="card-hint" v-else>Активного вызова нет. Ожидайте назначение.</p>
        </section>
        <section class="map-area">
          <div class="map-frame">
            <svg ref="svgEl" viewBox="-40 -40 980 600"></svg>
            <div class="map-tip" v-if="tip">{{ tip }}</div>
          </div>
        </section>
      </main>
    </div>
  `,
};

const AdminView = {
  components: { TopBar },
  setup() {
    const tab = ref('monitor');
    const monitor = ref(null);
    const state = ref(null);
    const error = ref('');
    const modal = ref(null);
    const form = ref({});
    const launching = ref('');
    const svgEl = ref(null);
    const tip = ref('');
    let map = null;
    let reference = null;

    async function loadMonitor() {
      monitor.value = await request('/admin/monitor');
    }

    async function loadDispatch() {
      state.value = await request('/state');
      if (!reference) reference = await request('/reference');
      await nextTick();
      if (!svgEl.value) return;
      if (map) map.destroy();
      map = createMapView(svgEl.value, reference, {
        onEmployeeDrop: async (id, x, y) => {
          state.value = await request(`/employees/${id}/position`, { method: 'POST', body: { x, y } });
          paint();
        },
        onEmployeeHover: (employee) => { tip.value = employee.tooltip; },
        onEmployeeLeave: () => { tip.value = ''; },
      });
      paint();
    }

    function paint() {
      if (!map || !state.value) return;
      const proposal = state.value.proposal;
      map.render(state.value, {
        highlightEmployeeId: proposal?.selected?.employee_id,
        targetStandNodeId: proposal?.request?.stand_node_id,
      });
      if (proposal?.selected?.route) {
        map.drawRoute(proposal.selected.route, { standNodeId: proposal.request.stand_node_id });
      } else map.clearRoute();
    }

    async function refresh() {
      await loadMonitor();
      if (tab.value === 'dispatch') await loadDispatch();
    }

    async function impersonate(userId) {
      const result = await request('/auth/impersonate', { method: 'POST', body: { user_id: userId } });
      session.me = result.me;
      router.push({ name: homeOf(result.me.role) });
    }

    function openModal(kind, row = {}) {
      modal.value = kind;
      const copy = { ...row };
      if (Array.isArray(copy.qualifications)) copy.qualifications = copy.qualifications.join(', ');
      if (Array.isArray(copy.type_ratings)) copy.type_ratings = copy.type_ratings.join(', ');
      if (copy.status && typeof copy.status === 'object') copy.status = copy.status.id;
      copy.status_id = copy.status_id || copy.status;
      form.value = copy;
      error.value = '';
    }

    async function saveEmployee() {
      try {
        const payload = {
          name: form.value.name,
          qualifications: Array.isArray(form.value.qualifications)
            ? form.value.qualifications
            : String(form.value.qualifications || '').split(/[,\s]+/).filter(Boolean),
          type_ratings: Array.isArray(form.value.type_ratings)
            ? form.value.type_ratings
            : String(form.value.type_ratings || '').split(/[,\s]+/).filter(Boolean),
          status: form.value.status,
        };
        if (modal.value === 'employee-new') {
          await request('/admin/employees', {
            method: 'POST',
            body: { id: form.value.id, home_node_id: form.value.home_node_id || 'TC', ...payload },
          });
        } else {
          await request(`/admin/employees/${form.value.id}`, { method: 'PATCH', body: payload });
        }
        modal.value = null;
        await loadMonitor();
      } catch (err) { error.value = err.message; }
    }

    async function saveAircraft() {
      try {
        const payload = {
          type_id: form.value.type_id,
          stand_node_id: form.value.stand_node_id,
          flight_number: form.value.flight_number,
          captain_name: form.value.captain_name,
          status: form.value.status_id || form.value.status,
        };
        if (modal.value === 'aircraft-new') {
          await request('/admin/aircraft', { method: 'POST', body: { id: form.value.id, ...payload } });
        } else {
          await request(`/admin/aircraft/${form.value.id}`, { method: 'PATCH', body: payload });
        }
        modal.value = null;
        await loadMonitor();
      } catch (err) { error.value = err.message; }
    }

    async function saveVehicle() {
      try {
        await request(`/admin/vehicles/${form.value.id}`, {
          method: 'PATCH',
          body: { status: form.value.status?.id || form.value.status, type_id: form.value.type_id },
        });
        modal.value = null;
        await loadMonitor();
      } catch (err) { error.value = err.message; }
    }

    async function callSpecialist() {
      state.value = await request('/calls', {
        method: 'POST',
        body: {
          aircraft_id: state.value.form.aircraft_id || state.value.aircraft[0].id,
          fault_type_id: state.value.form.fault_type_id || 'NAV_IRS_FAIL',
          policy: state.value.form.policy,
        },
      });
      paint();
      await loadMonitor();
    }

    async function cancelRequest(requestId) {
      monitor.value = await request(`/admin/requests/${requestId}/cancel`, { method: 'POST' });
      if (tab.value === 'dispatch') await loadDispatch();
    }

    async function completeRequest(requestId) {
      monitor.value = await request(`/admin/requests/${requestId}/complete`, { method: 'POST' });
      if (tab.value === 'dispatch') await loadDispatch();
    }

    async function removeEmployee(id) {
      if (!confirm(`Удалить сотрудника ${id}?`)) return;
      await request(`/admin/employees/${id}`, { method: 'DELETE' });
      await loadMonitor();
    }

    async function removeVehicle(id) {
      if (!confirm(`Удалить технику ${id}?`)) return;
      await request(`/admin/vehicles/${id}`, { method: 'DELETE' });
      await loadMonitor();
    }

    async function saveFault() {
      try {
        await request('/admin/faults', {
          method: 'POST',
          body: {
            id: form.value.id,
            ata: form.value.ata || '00',
            name: form.value.name,
            description: form.value.description || '',
            primary_any_of: String(form.value.primary_any_of || '').split(/[,\s]+/).filter(Boolean),
            default_priority: form.value.default_priority || 'NORMAL',
            estimated_work_min: Number(form.value.estimated_work_min) || 30,
          },
        });
        modal.value = null;
        await loadMonitor();
      } catch (err) { error.value = err.message; }
    }

    async function removeFault(id) {
      if (!confirm(`Удалить неисправность ${id}?`)) return;
      try {
        await request(`/admin/faults/${id}`, { method: 'DELETE' });
        await loadMonitor();
      } catch (err) { error.value = err.message; }
    }

    async function launchScenario(code) {
      launching.value = code;
      error.value = '';
      try {
        state.value = await request(`/scenarios/${code}/load`, { method: 'POST' });
        await loadMonitor();
        tab.value = 'dispatch';
        await nextTick();
        await loadDispatch();
      } catch (err) {
        error.value = err.message;
      } finally {
        launching.value = '';
      }
    }

    watch(tab, async (value) => {
      if (value === 'dispatch') {
        await nextTick();
        await loadDispatch();
      }
    });

    onMounted(refresh);
    const timer = setInterval(() => { if (tab.value === 'monitor') loadMonitor().catch(() => {}); }, 4000);
    onBeforeUnmount(() => {
      clearInterval(timer);
      if (map) map.destroy();
    });

    return {
      tab, monitor, state, error, modal, form, svgEl, launching, tip,
      impersonate, openModal, saveEmployee, saveAircraft, saveVehicle, saveFault,
      callSpecialist, refresh, launchScenario,
      cancelRequest, completeRequest, removeEmployee, removeVehicle, removeFault,
    };
  },
  template: `
    <div class="role-shell">
      <TopBar subtitle="Рабочее место диспетчера" />
      <main class="role-main wide" v-if="monitor">
        <div class="admin-tabs">
          <button class="tab" :class="{ active: tab === 'monitor' }" @click="tab = 'monitor'">Мониторинг</button>
          <button class="tab" :class="{ active: tab === 'people' }" @click="tab = 'people'">Персонал</button>
          <button class="tab" :class="{ active: tab === 'fleet' }" @click="tab = 'fleet'">Борта</button>
          <button class="tab" :class="{ active: tab === 'vehicles' }" @click="tab = 'vehicles'">Техника</button>
          <button class="tab" :class="{ active: tab === 'faults' }" @click="tab = 'faults'">Неисправности</button>
          <button class="tab" :class="{ active: tab === 'dispatch' }" @click="tab = 'dispatch'">Диспетчеризация</button>
          <button class="tab" :class="{ active: tab === 'scenarios' }" @click="tab = 'scenarios'">Сценарии</button>
        </div>

        <section v-if="tab === 'monitor'">
          <div class="stat-row">
            <div class="stat"><div class="n">{{ monitor.counts.available }}</div><div class="l">свободны</div></div>
            <div class="stat"><div class="n">{{ monitor.counts.busy }}</div><div class="l">заняты</div></div>
            <div class="stat"><div class="n">{{ monitor.counts.pending }}</div><div class="l">в ожидании</div></div>
            <div class="stat"><div class="n">{{ monitor.requests.length }}</div><div class="l">активных заявок</div></div>
          </div>
          <table class="crm-table">
            <thead><tr><th>Инженер</th><th>Статус</th><th>Задача</th><th>На работе</th><th></th></tr></thead>
            <tbody>
              <tr v-for="row in monitor.employees" :key="row.id">
                <td>{{ row.name }}<div class="quiet">{{ row.id }} · {{ row.qualifications.join(', ') }}</div></td>
                <td>{{ row.busy_label || row.status_name }}</td>
                <td>{{ row.task || '—' }} <span class="quiet" v-if="row.aircraft_id">{{ row.aircraft_id }}</span></td>
                <td>{{ row.worked_text || '—' }}</td>
                <td>
                  <button class="as-link" v-if="row.user_id" @click="impersonate(row.user_id)">Войти как…</button>
                  <button class="as-link" v-if="row.request_id" @click="completeRequest(row.request_id)">Завершить</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="tab === 'people'">
          <div class="actions" style="margin-bottom:10px">
            <button class="btn btn-outline btn-small" @click="openModal('employee-new', { qualifications: ['B1'], type_ratings: ['A320'], status: 'AVAILABLE' })">Добавить сотрудника</button>
          </div>
          <table class="crm-table">
            <thead><tr><th>Сотрудник</th><th>Квалификации</th><th>Допуски</th><th>Статус</th><th></th></tr></thead>
            <tbody>
              <tr v-for="row in monitor.employees" :key="row.id">
                <td>{{ row.name }}</td>
                <td>{{ row.qualifications.join(', ') }}</td>
                <td class="quiet">{{ row.id }}</td>
                <td>{{ row.busy_label || row.status_name }}</td>
                <td>
                  <button class="as-link" @click="openModal('employee', row)">Изменить</button>
                  <button class="as-link" v-if="row.user_id" @click="impersonate(row.user_id)">Войти как…</button>
                  <button class="as-link" @click="removeEmployee(row.id)">Удалить</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="tab === 'fleet'">
          <div class="actions" style="margin-bottom:10px">
            <button class="btn btn-outline btn-small" @click="openModal('aircraft-new', { type_id: 'A320', stand_node_id: 'A3', status: 'ON_STAND' })">Добавить борт</button>
          </div>
          <table class="crm-table">
            <thead><tr><th>Борт</th><th>Тип</th><th>Стоянка</th><th>Статус</th><th></th></tr></thead>
            <tbody>
              <tr v-for="row in monitor.aircraft" :key="row.id">
                <td>{{ row.id }}<div class="quiet">{{ row.flight_number }}</div></td>
                <td>{{ row.type_name }}</td>
                <td>{{ row.stand_name }}</td>
                <td>{{ row.status.name }}</td>
                <td>
                  <button class="as-link" @click="openModal('aircraft', row)">Изменить</button>
                  <button class="as-link" v-if="row.user_id" @click="impersonate(row.user_id)">Войти как…</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="tab === 'faults'">
          <div class="actions" style="margin-bottom:10px">
            <button class="btn btn-outline btn-small" @click="openModal('fault-new', { ata: '24', primary_any_of: 'B1', default_priority: 'NORMAL', estimated_work_min: 30 })">Добавить неисправность</button>
          </div>
          <table class="crm-table">
            <thead><tr><th>Код</th><th>Название</th><th>Квалификация</th><th>Приоритет</th><th></th></tr></thead>
            <tbody>
              <tr v-for="row in monitor.catalog.fault_types" :key="row.id">
                <td>{{ row.id }}<div class="quiet">ATA {{ row.ata }}</div></td>
                <td>{{ row.name }}</td>
                <td>{{ row.primary_any_of.join(', ') }}</td>
                <td>{{ row.default_priority }}</td>
                <td><button class="as-link" @click="removeFault(row.id)">Удалить</button></td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="tab === 'vehicles'">
          <table class="crm-table">
            <thead><tr><th>Борт техники</th><th>Тип</th><th>Статус</th><th></th></tr></thead>
            <tbody>
              <tr v-for="row in monitor.vehicles" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.type_name }}</td>
                <td>{{ row.status.name }}</td>
                <td>
                  <button class="as-link" @click="openModal('vehicle', row)">Изменить</button>
                  <button class="as-link" @click="removeVehicle(row.id)">Удалить</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="tab === 'scenarios'">
          <p class="card-hint">Быстрый запуск перестраивает базу: расстановка сотрудников, статусы и перекрытия дорог. Карта обновится сразу.</p>
          <div class="login-error" v-if="error">{{ error }}</div>
          <div class="scenario-grid">
            <article class="scenario-card" v-for="item in monitor.scenarios" :key="item.code">
              <div class="scenario-head">
                <b>{{ item.code }}</b>
                <span class="quiet">{{ item.covers.join(' · ') }}</span>
              </div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.purpose }}</p>
              <p class="quiet">Дано: {{ item.given }}</p>
              <p class="quiet">Ожидание: {{ item.expected }}</p>
              <button
                class="btn btn-primary btn-small"
                type="button"
                :disabled="launching === item.code"
                @click="launchScenario(item.code)"
              >{{ launching === item.code ? 'Загрузка…' : 'Быстрый запуск сценария' }}</button>
            </article>
          </div>
        </section>

        <section v-show="tab === 'dispatch'" class="employee-layout">
          <div class="card">
            <h2>Вызов</h2>
            <p class="card-hint" v-if="monitor.active_scenario">
              Загружен {{ monitor.active_scenario.code }}: {{ monitor.active_scenario.title }}.
              Нажмите «Вызвать» — система сразу назначит свободного инженера.
            </p>
            <p class="card-hint" v-else>Назначение происходит сразу, без подтверждения.</p>
            <label class="field" v-if="state"><span>Борт</span>
              <select v-model="state.form.aircraft_id">
                <option v-for="ac in state.aircraft" :key="ac.id" :value="ac.id">{{ ac.id }}</option>
              </select>
            </label>
            <label class="field" v-if="state"><span>Неисправность</span>
              <select v-model="state.form.fault_type_id">
                <option v-for="item in monitor.catalog.fault_types" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
            </label>
            <button class="btn btn-primary" type="button" @click="callSpecialist">Вызвать</button>
            <div class="call-card" v-if="state?.proposal?.committed && state.proposal.selected">
              <h3>{{ state.proposal.headline }}</h3>
              <p>{{ state.proposal.selected.employee_name }} · {{ state.proposal.selected.eta_text }}</p>
              <button class="btn btn-outline btn-small" type="button" @click="cancelRequest(state.proposal.request.id)">Отменить заявку</button>
            </div>
            <div class="call-card" v-else-if="state?.proposal?.request?.status?.id === 'PENDING'">
              <h3>В ожидании</h3>
              <p>Все подходящие специалисты заняты. Заявка встанет в работу, как только кто-то освободится.</p>
              <button class="btn btn-outline btn-small" type="button" @click="cancelRequest(state.proposal.request.id)">Отменить заявку</button>
            </div>
            <div class="call-card" v-else-if="state?.proposal?.escalation">
              <h3>Эскалация</h3>
              <p>{{ state.proposal.escalation.reason }}</p>
            </div>
          </div>
          <div class="map-frame">
            <svg ref="svgEl" viewBox="-40 -40 980 600"></svg>
            <div class="map-tip" v-if="tip">{{ tip }}</div>
          </div>
        </section>

        <div class="modal-back" v-if="modal" @click.self="modal = null">
          <div class="modal">
            <h3 v-if="modal.startsWith('employee')">Сотрудник</h3>
            <h3 v-else-if="modal.startsWith('aircraft')">Воздушное судно</h3>
            <h3 v-else-if="modal.startsWith('fault')">Неисправность</h3>
            <h3 v-else>Спецтранспорт</h3>
            <div class="login-error" v-if="error">{{ error }}</div>

            <template v-if="modal.startsWith('employee')">
              <label class="field" v-if="modal === 'employee-new'"><span>Табельный номер</span><input v-model="form.id"></label>
              <label class="field"><span>ФИО</span><input v-model="form.name"></label>
              <label class="field"><span>Квалификации</span>
                <input v-model="form.qualifications" placeholder="B1, A">
              </label>
              <label class="field"><span>Допуски на типы ВС</span>
                <input v-model="form.type_ratings" placeholder="SU95, A320">
              </label>
              <div class="actions"><button class="btn btn-ok" @click="saveEmployee">Сохранить</button></div>
            </template>

            <template v-else-if="modal.startsWith('fault')">
              <label class="field"><span>Код</span><input v-model="form.id" placeholder="HYD_LEAK_2"></label>
              <label class="field"><span>ATA</span><input v-model="form.ata"></label>
              <label class="field"><span>Название</span><input v-model="form.name"></label>
              <label class="field"><span>Квалификации</span><input v-model="form.primary_any_of" placeholder="B1, B2"></label>
              <label class="field"><span>Приоритет</span>
                <select v-model="form.default_priority">
                  <option v-for="item in monitor.catalog.priorities" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
              </label>
              <label class="field"><span>Оценка работ, мин</span><input v-model="form.estimated_work_min"></label>
              <div class="actions"><button class="btn btn-ok" @click="saveFault">Сохранить</button></div>
            </template>

            <template v-else-if="modal.startsWith('aircraft')">
              <label class="field" v-if="modal === 'aircraft-new'"><span>Бортовой номер</span><input v-model="form.id"></label>
              <label class="field"><span>Тип</span>
                <select v-model="form.type_id">
                  <option v-for="item in monitor.catalog.aircraft_types" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
              </label>
              <label class="field"><span>Стоянка</span>
                <select v-model="form.stand_node_id">
                  <option v-for="item in monitor.catalog.stands" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
              </label>
              <label class="field"><span>Рейс</span><input v-model="form.flight_number"></label>
              <label class="field"><span>КВС</span><input v-model="form.captain_name"></label>
              <label class="field"><span>Статус</span>
                <select v-model="form.status_id">
                  <option v-for="item in monitor.catalog.aircraft_statuses" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
              </label>
              <div class="actions"><button class="btn btn-ok" @click="saveAircraft">Сохранить</button></div>
            </template>

            <template v-else>
              <label class="field"><span>Статус</span>
                <select v-model="form.status">
                  <option v-for="item in monitor.catalog.vehicle_statuses" :key="item.id" :value="item.id">{{ item.name }}</option>
                </select>
              </label>
              <div class="actions"><button class="btn btn-ok" @click="saveVehicle">Сохранить</button></div>
            </template>
          </div>
        </div>
      </main>
    </div>
  `,
};

const routes = [
  { path: '/', redirect: '/login' },
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
  { path: '/admin', name: 'admin', component: AdminView, meta: { role: 'admin' } },
  { path: '/employee', name: 'employee', component: EmployeeView, meta: { role: 'employee' } },
  { path: '/aircraft', name: 'aircraft', component: AircraftView, meta: { role: 'aircraft' } },
];

const router = createRouter({ history: createWebHashHistory(), routes });

router.beforeEach(async (to) => {
  if (to.meta.public) return true;
  if (!session.me) {
    try { session.me = await request('/auth/me'); } catch { return { name: 'login' }; }
  }
  if (to.meta.role && session.me.role !== to.meta.role) {
    return { name: homeOf(session.me.role) };
  }
  return true;
});

createApp({ template: '<router-view />' }).use(router).mount('#app');
