import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { PartsData } from "../api";

// The drawing's own parts as layers in 3D: each part's strokes (cut out by app/parts.py) on a translucent card,
// placed where the drawing has it ("exploded") or slid back along the drawing's assembly axis onto one stack
// ("assembled"). Depth 0 is the outermost part and sits nearest the camera.

type Props = {
  runId: string;
  data: PartsData;
  exploded: boolean;
  autoRotate: boolean;
  selected: number | null;
  onSelect: (index: number | null) => void;
  onHover?: (index: number | null) => void;
  resetKey?: number;
};

const WORLD = 9; // the parts' extent in world units
const GAP = 1.5; // spacing between layers when exploded

/** whether the page is in dark mode (explicit choice or the system's), updated live */
function useDark() {
  const get = () => {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const [dark, setDark] = useState(get);
  useEffect(() => {
    const update = () => setDark(get());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => { mq.removeEventListener("change", update); mo.disconnect(); };
  }, []);
  return dark;
}

/** the part's black strokes drawn light, for dark cards (alpha kept) */
function lightStrokes(texture: THREE.Texture) {
  const img = texture.image as HTMLImageElement;
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const g = c.getContext("2d")!;
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) { d.data[i] = 255 - d.data[i]; d.data[i + 1] = 255 - d.data[i + 1]; d.data[i + 2] = 255 - d.data[i + 2]; }
  g.putImageData(d, 0, 0);
  texture.image = c;
  texture.needsUpdate = true;
}

export function Exploded3D({ runId, data, exploded, autoRotate, selected, onSelect, onHover, resetKey }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const labels = useRef<HTMLDivElement>(null);
  const state = useRef<{ exploded: boolean; autoRotate: boolean; selected: number | null; hovered: number | null }>({
    exploded, autoRotate, selected, hovered: null,
  });
  const resetView = useRef<() => void>(() => {});
  state.current.exploded = exploded;
  state.current.autoRotate = autoRotate;
  state.current.selected = selected;

  useEffect(() => resetView.current(), [resetKey]);
  const dark = useDark();

  useEffect(() => {
    const el = mount.current!;
    // frame the parts themselves (the callout column was cut away): centre on them and scale them to WORLD units
    const minX = Math.min(...data.crops.map((c) => c[0]));
    const maxX = Math.max(...data.crops.map((c) => c[2]));
    const minY = Math.min(...data.crops.map((c) => c[1]));
    const maxY = Math.max(...data.crops.map((c) => c[3]));
    const W = minX + maxX; // so that W / 2 is the parts' centre in x
    const H = minY + maxY;
    const scale = WORLD / Math.max(1, maxX - minX, (maxY - minY) * 1.3);
    const n = data.parts.length;
    const ACTION = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue("--action").trim() || "#cc4a0a");
    const CARD = new THREE.Color(dark ? 0x2a2d33 : 0xffffff);
    const EDGE = dark ? 0x4a4e57 : 0xd0d1d2;
    const meanDepth = (n - 1) / 2;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 200);
    const home = new THREE.Vector3(-7.6, 3.6, 10);
    camera.position.copy(home);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 5;
    controls.maxDistance = 40;
    resetView.current = () => {
      camera.position.copy(home);
      controls.target.set(0, 0, 0);
      controls.update();
    };

    // assembly axis in world coordinates (image y points down)
    const axis = new THREE.Vector2(data.axis[0], -data.axis[1]);
    if (axis.lengthSq() < 1e-6) axis.set(1, -1);
    axis.normalize();
    const centres = data.crops.map(([x0, y0, x1, y1]) => new THREE.Vector2(((x0 + x1) / 2 - W / 2) * scale, (H / 2 - (y0 + y1) / 2) * scale));
    const mid = centres.reduce((a, c) => a.add(c.clone()), new THREE.Vector2()).multiplyScalar(1 / Math.max(1, n));

    type Layer = { group: THREE.Group; card: THREE.Mesh; edge: THREE.LineSegments; art: THREE.Mesh; exploded: THREE.Vector3; assembled: THREE.Vector3 };
    const loader = new THREE.TextureLoader();
    const layers: Layer[] = data.parts.map((p, i) => {
      const [x0, y0, x1, y1] = data.crops[i];
      const w = Math.max(0.05, (x1 - x0) * scale);
      const h = Math.max(0.05, (y1 - y0) * scale);
      const group = new THREE.Group();
      const pad = 0.12;
      const card = new THREE.Mesh(
        new THREE.PlaneGeometry(w + pad * 2, h + pad * 2),
        new THREE.MeshBasicMaterial({ color: CARD, transparent: true, opacity: 0.35, depthWrite: false, side: THREE.DoubleSide }),
      );
      card.userData.index = i;
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(card.geometry as THREE.PlaneGeometry),
        new THREE.LineBasicMaterial({ color: EDGE, transparent: true, opacity: 0.9 }),
      );
      const texture = loader.load(`/api/runs/${runId}/parts/${i}.png`, dark ? lightStrokes : undefined);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      const art = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide }),
      );
      art.position.z = 0.002;
      group.add(card, edge, art);
      scene.add(group);
      const z = (meanDepth - p.depth) * GAP;
      const c = centres[i];
      const s = c.clone().sub(mid).dot(axis); // how far along the axis the drawing pulled this part out
      const onAxis = c.clone().sub(axis.clone().multiplyScalar(s));
      const layer = {
        group, card, edge, art,
        exploded: new THREE.Vector3(c.x, c.y, z),
        assembled: new THREE.Vector3(onAxis.x, onAxis.y, (meanDepth - p.depth) * 0.06),
      };
      group.position.copy(state.current.exploded ? layer.exploded : layer.assembled);
      return layer;
    });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let down = { x: 0, y: 0 };
    const pick = (ev: PointerEvent): number | null => {
      const r = renderer.domElement.getBoundingClientRect();
      pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(layers.map((l) => l.card))[0];
      return hit ? (hit.object.userData.index as number) : null;
    };
    const onMove = (ev: PointerEvent) => {
      const i = pick(ev);
      if (i !== state.current.hovered) {
        state.current.hovered = i;
        onHover?.(i);
        renderer.domElement.style.cursor = i === null ? "grab" : "pointer";
      }
    };
    const onDown = (ev: PointerEvent) => (down = { x: ev.clientX, y: ev.clientY });
    const onUp = (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 5) return; // a drag, not a click
      const i = pick(ev);
      onSelect(i === state.current.selected ? null : i);
    };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    let t = state.current.exploded ? 1 : 0;
    let frame = 0;
    let clock = 0;
    const tmp = new THREE.Vector3();
    const animate = () => {
      frame = requestAnimationFrame(animate);
      clock += 1 / 60;
      const goal = state.current.exploded ? 1 : 0;
      t += (goal - t) * 0.06;
      const ease = t * t * (3 - 2 * t);
      const focus = state.current.selected ?? state.current.hovered;
      layers.forEach((l, i) => {
        tmp.copy(l.assembled).lerp(l.exploded, ease);
        l.group.position.lerp(tmp, 0.35);
        const active = focus === i;
        const dim = focus !== null && !active;
        (l.art.material as THREE.MeshBasicMaterial).opacity = dim ? 0.18 : 1;
        const card = l.card.material as THREE.MeshBasicMaterial;
        card.color.copy(active ? ACTION : CARD);
        card.opacity = active ? 0.16 : dim ? 0.1 : 0.35;
        (l.edge.material as THREE.LineBasicMaterial).color.set(active ? ACTION : EDGE);
        l.group.scale.setScalar(THREE.MathUtils.lerp(l.group.scale.x, active ? 1.04 : 1, 0.2));
      });
      if (state.current.autoRotate) {
        const yaw = Math.sin(clock * 0.35) * 0.35;
        const r = Math.hypot(home.x, home.z);
        camera.position.x = THREE.MathUtils.lerp(camera.position.x, Math.sin(Math.atan2(home.x, home.z) + yaw) * r, 0.03);
        camera.position.z = THREE.MathUtils.lerp(camera.position.z, Math.cos(Math.atan2(home.x, home.z) + yaw) * r, 0.03);
      }
      controls.update();
      renderer.render(scene, camera);
      // part names float next to their layer while exploded
      const box = labels.current;
      if (box) {
        const r = renderer.domElement.getBoundingClientRect();
        Array.from(box.children).forEach((child, i) => {
          const l = layers[i];
          if (!l) return;
          const [cx0, cy0, cx1, cy1] = data.crops[i];
          tmp.set(l.group.position.x + ((cx1 - cx0) * scale) / 2, l.group.position.y + ((cy1 - cy0) * scale) / 2, l.group.position.z).project(camera);
          const el2 = child as HTMLElement;
          el2.style.transform = `translate(${((tmp.x + 1) / 2) * r.width}px, ${((1 - tmp.y) / 2) * r.height}px)`;
          el2.style.opacity = String(Math.max(0, ease - 0.4) / 0.6 * (focus === null || focus === i ? 1 : 0.35));
        });
      }
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      layers.forEach((l) => {
        [l.card, l.art].forEach((m) => {
          m.geometry.dispose();
          const mat = m.material as THREE.MeshBasicMaterial;
          mat.map?.dispose();
          mat.dispose();
        });
        l.edge.geometry.dispose();
        (l.edge.material as THREE.Material).dispose();
      });
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
    // the scene is rebuilt only when the parts change; the props above are read through `state`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, data, dark]);

  return (
    <div className="viewer3d" ref={mount}>
      <div className="viewer3d-labels" ref={labels} aria-hidden>
        {data.parts.map((p, i) => (
          <span key={i} className={`viewer3d-label${selected === i ? " active" : ""}`}>{p.name}</span>
        ))}
      </div>
    </div>
  );
}
