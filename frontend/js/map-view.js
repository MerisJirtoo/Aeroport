/**
 * SVG-карта аэропорта
 */

import { clear, svg } from './dom.js';

const AIRCRAFT_PATH =
  'M0,-20 C2,-20 4,-16 4,-10 L4,-6 L20,4 L20,8 L4,3 L4,12 ' +
  'L9,16 L9,19 L0,17 L-9,19 L-9,16 L-4,12 L-4,3 L-20,8 L-20,4 L-4,-6 L-4,-10 ' +
  'C-4,-16 -2,-20 0,-20 Z';

const LAYER_ORDER = ['edges', 'nodes', 'route', 'nodeLabels', 'aircraft', 'vehicles', 'employees'];

export function createMapView(svgEl, reference, handlers = {}) {
  const map = reference.map;
  const extent = map.extent;
  const nodeById = new Map(map.nodes.map((node) => [node.id, node]));
  const qualificationColor = new Map(
    reference.qualifications.map((q) => [q.id, q.color]),
  );

  const layers = {};
  let drag = null;

  // -------------------------------------------------------------------------
  // Каркас
  // -------------------------------------------------------------------------

  function buildSkeleton() {
    clear(svgEl);

    svgEl.appendChild(svg('rect', {
      class: 'apron-bg',
      x: -40, y: -40,
      width: extent.width + 80, height: extent.height + 80, rx: 14,
    }));

    svgEl.appendChild(svg('rect', {
      class: 'terminal', x: 120, y: -34, width: 700, height: 48, rx: 8,
    }));
    svgEl.appendChild(svg('text', {
      class: 'terminal-text', x: 470, y: -3, 'text-anchor': 'middle',
      text: 'ПАССАЖИРСКИЙ ТЕРМИНАЛ',
    }));

    for (const name of LAYER_ORDER) {
      layers[name] = svg('g', { class: `layer-${name}` });
      svgEl.appendChild(layers[name]);
    }
  }

  // -------------------------------------------------------------------------
  // Дороги и контрольные точки
  // -------------------------------------------------------------------------

  function renderEdges(closedEdgeIds) {
    const layer = clear(layers.edges);
    const closed = new Set(closedEdgeIds);

    for (const edge of map.edges) {
      const a = nodeById.get(edge.from_node_id);
      const b = nodeById.get(edge.to_node_id);
      const line = { x1: a.x, y1: a.y, x2: b.x, y2: b.y };

      if (closed.has(edge.id)) {
        layer.appendChild(svg('line', { class: 'edge-closed', ...line }, [
          svg('title', { text: `Участок перекрыт: ${edge.name}` }),
        ]));
        continue;
      }

      const title = svg('title', { text: `${edge.name} · ${edge.hint}` });
      if (edge.type === 'SERVICE_ROAD') {
        layer.appendChild(svg('line', { class: 'edge-road', ...line }, [title]));
        layer.appendChild(svg('line', { class: 'edge-road-center', ...line }));
      } else if (edge.type === 'STAND_LANE') {
        layer.appendChild(svg('line', { class: 'edge-lane', ...line }, [title]));
      } else {
        layer.appendChild(svg('line', { class: 'edge-foot', ...line }, [title]));
      }
    }
  }

  function renderNodes() {
    const shapes = clear(layers.nodes);
    const labels = clear(layers.nodeLabels);

    const label = (cls, x, y, text) => labels.appendChild(
      svg('text', { class: cls, x, y, 'text-anchor': 'middle', text }),
    );

    for (const node of map.nodes) {
      const group = svg('g', {}, [
        svg('title', { text: `${node.name}. ${node.description}` }),
      ]);

      if (node.type === 'STAND') {
        group.appendChild(svg('rect', {
          class: 'node-stand', x: node.x - 46, y: node.y - 44,
          width: 92, height: 92, rx: 10,
        }));
        label('node-label', node.x, node.y - 52, node.short);
        label('node-sub', node.x, node.y + 60,
          `кат. ${node.stand.category}${node.stand.jet_bridge ? ' · телетрап' : ''}`);
      } else if (node.type === 'JUNCTION') {
        group.appendChild(svg('circle', { class: 'node-junction', cx: node.x, cy: node.y, r: 9 }));
        label('node-sub', node.x, node.y + 26, node.short);
      } else {
        group.appendChild(svg('rect', {
          class: 'node-facility', x: node.x - 44, y: node.y - 26,
          width: 88, height: 52, rx: 8,
        }));
        label('node-label', node.x, node.y - 34, node.short);
      }

      shapes.appendChild(group);
    }
  }

  // -------------------------------------------------------------------------
  // Динамические слои
  // -------------------------------------------------------------------------

  function renderAircraft(state, view) {
    const layer = clear(layers.aircraft);

    for (const aircraft of state.aircraft) {
      const stand = nodeById.get(aircraft.stand_node_id);
      const scale = aircraft.map_scale;
      const classes = ['aircraft'];
      if (aircraft.status.id === 'AOG') classes.push('aircraft-aog');
      if (view.targetStandNodeId === aircraft.stand_node_id) classes.push('aircraft-target');

      layer.appendChild(svg('g', {
        class: classes.join(' '),
        transform: `translate(${stand.x},${stand.y - 4}) scale(${scale})`,
      }, [
        svg('title', { text: aircraft.tooltip }),
        svg('path', { class: 'aircraft-body', d: AIRCRAFT_PATH }),
        svg('rect', { class: 'aircraft-tail', x: -3.2, y: 11, width: 6.4, height: 7, rx: 1 }),
        svg('text', {
          class: 'aircraft-label', x: 0, y: 38, 'text-anchor': 'middle',
          transform: `scale(${1 / scale})`, text: aircraft.id,
        }),
      ]));
    }
  }

  function renderVehicles(state) {
    const layer = clear(layers.vehicles);

    for (const vehicle of state.vehicles) {
      layer.appendChild(svg('g', { transform: `translate(${vehicle.x},${vehicle.y})` }, [
        svg('title', { text: vehicle.tooltip }),
        svg('rect', {
          class: `vehicle-box${vehicle.available ? '' : ' busy'}`,
          x: -13, y: -8, width: 26, height: 16, rx: 4,
        }),
        svg('text', {
          class: 'vehicle-glyph', x: 0, y: 4, 'text-anchor': 'middle', text: vehicle.glyph,
        }),
      ]));
    }
  }
  function pickLabelOffset(x, placed) {
    for (const dy of [28, 41, 54, 67, 80]) {
      const free = placed.every((box) => Math.abs(box.x - x) > 58 || Math.abs(box.dy - dy) >= 13);
      if (free) {
        placed.push({ x, dy });
        return dy;
      }
    }
    return 28;
  }

  function renderEmployees(state, view) {
    const layer = clear(layers.employees);
    const placedLabels = [];

    for (const employee of state.employees) {
      const assigned = view.highlightEmployeeId === employee.id;
      const color = qualificationColor.get(employee.qualifications[0].id);

      const group = svg('g', {
        class: `emp${assigned ? ' is-assigned' : ''}`,
        transform: `translate(${employee.x},${employee.y})`,
        dataset: { employeeId: employee.id },
      }, [
        svg('title', { text: employee.tooltip || '' }),
        svg('circle', {
          class: 'emp-halo', r: 15,
          stroke: assigned ? null : employee.status.color,
        }),
        svg('circle', { class: 'emp-body', r: 11, fill: color }),
        svg('text', {
          class: 'emp-initials', y: 3.5, 'text-anchor': 'middle', text: employee.initials,
        }),
        svg('text', {
          class: 'emp-name', y: pickLabelOffset(employee.x, placedLabels),
          'text-anchor': 'middle', text: employee.surname,
        }),
      ]);

      layer.appendChild(group);
    }
  }

  // -------------------------------------------------------------------------
  // Маршрут
  // -------------------------------------------------------------------------

  function drawRoute(route, { peek = false, standNodeId = null } = {}) {
    const layer = clear(layers.route);
    if (!route) return;

    if (!route.points.length) {
      const stand = standNodeId ? nodeById.get(standNodeId) : null;
      if (stand) {
        layer.appendChild(svg('circle', {
          class: 'route-here', cx: stand.x, cy: stand.y, r: 30,
        }, [svg('title', { text: 'Исполнитель уже находится у борта' })]));
      }
      return;
    }

    const d = route.points
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(' ');
    const suffix = peek ? ' peek' : '';

    layer.appendChild(svg('path', { class: `route-glow${suffix}`, d }));
    layer.appendChild(svg('path', { class: `route-line${suffix}`, d }));

    const last = route.points[route.points.length - 1];
    layer.appendChild(svg('circle', { class: `route-pin${suffix}`, cx: last.x, cy: last.y, r: 5 }));
  }

  function clearRoute() {
    clear(layers.route);
  }

  // -------------------------------------------------------------------------
  // Перетаскивание сотрудников
  // -------------------------------------------------------------------------

  function toMapPoint(event) {
    const point = svgEl.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svgEl.getScreenCTM().inverse());
  }

  function clampCoord(value, limit) {
    return Math.round(Math.max(0, Math.min(limit, value)) * 10) / 10;
  }

  function capturePointer(pointerId) {
    try {
      svgEl.setPointerCapture(pointerId);
    } catch (error) {
      console.warn('Захват указателя недоступен, перенос без него');
    }
  }

  function releasePointer(pointerId) {
    try {
      if (svgEl.hasPointerCapture(pointerId)) svgEl.releasePointerCapture(pointerId);
    } catch (error) {}
  }

  function onPointerDown(event) {
    const group = event.target.closest?.('.emp');
    if (!group) return;

    event.preventDefault();
    capturePointer(event.pointerId);
    group.classList.add('dragging');
    layers.employees.appendChild(group);

    drag = { employeeId: group.dataset.employeeId, group, pointerId: event.pointerId, moved: false };
  }

  function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;

    const point = toMapPoint(event);
    const x = clampCoord(point.x, extent.width);
    const y = clampCoord(point.y, extent.height);

    drag.group.setAttribute('transform', `translate(${x},${y})`);
    drag.x = x;
    drag.y = y;
    drag.moved = true;

    handlers.onEmployeeDrag?.(drag.employeeId, x, y);
  }

  function onPointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;

    drag.group.classList.remove('dragging');
    releasePointer(event.pointerId);

    const finished = drag;
    drag = null;
    if (finished.moved) {
      handlers.onEmployeeDrop?.(finished.employeeId, finished.x, finished.y);
    }
  }

  // -------------------------------------------------------------------------
  // Публичный интерфейс
  // -------------------------------------------------------------------------

  buildSkeleton();
  renderNodes();
  renderEdges([]);

  function onEmployeeEnter(event) {
    const group = event.target.closest?.('.emp');
    if (!group || !handlers.onEmployeeHover) return;
    const employee = (lastState?.employees || []).find((item) => item.id === group.dataset.employeeId);
    if (employee) handlers.onEmployeeHover(employee, event);
  }

  function onEmployeeLeave(event) {
    if (event.target.closest?.('.emp')) handlers.onEmployeeLeave?.();
  }

  let lastState = null;

  svgEl.addEventListener('pointerdown', onPointerDown);
  svgEl.addEventListener('pointermove', onPointerMove);
  svgEl.addEventListener('pointerup', onPointerUp);
  svgEl.addEventListener('pointercancel', onPointerUp);
  svgEl.addEventListener('pointerover', onEmployeeEnter);
  svgEl.addEventListener('pointerout', onEmployeeLeave);

  return {
    /**
     * @param {object} state
     * @param {object} view
     */
    render(state, view = {}) {
      lastState = state;
      renderEdges(state.closed_edge_ids);
      renderAircraft(state, view);
      renderVehicles(state);
      if (!drag) renderEmployees(state, view);
    },
    drawRoute,
    clearRoute,
    isDragging: () => drag !== null,
    destroy() {
      svgEl.removeEventListener('pointerdown', onPointerDown);
      svgEl.removeEventListener('pointermove', onPointerMove);
      svgEl.removeEventListener('pointerup', onPointerUp);
      svgEl.removeEventListener('pointercancel', onPointerUp);
      svgEl.removeEventListener('pointerover', onEmployeeEnter);
      svgEl.removeEventListener('pointerout', onEmployeeLeave);
    },
  };
}
