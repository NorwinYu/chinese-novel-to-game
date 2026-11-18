// Low-poly 3D pictures from build/looks.json: characters (with moods and poses), places, anchor stills and
// a diorama map. Everything is built from a fixed parameter schema (n2g/game/looks.py), so no image service
// is needed. Shapes are seeded by name, so a place looks the same every time it is shown.
import * as THREE from "./vendor/three.module.min.js";

export const MOODS = ["平静", "高兴", "生气", "难过", "惊讶"];

function rng(seedText) {
  let h = 2166136261;
  for (const c of String(seedText)) h = Math.imul(h ^ c.codePointAt(0), 16777619);
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const matCache = new Map();
function mat(color, opts = {}) {
  const key = color + JSON.stringify(opts);
  if (!matCache.has(key)) matCache.set(key, new THREE.MeshStandardMaterial({color, flatShading: true, roughness: 0.95, metalness: 0, ...opts}));
  return matCache.get(key);
}
function mesh(geo, color, opts) {
  const m = new THREE.Mesh(geo, mat(color, opts));
  m.castShadow = true; m.receiveShadow = true;
  return m;
}
const shade = (hex, k) => "#" + new THREE.Color(hex).multiplyScalar(k).getHexString();

/* ---------------- characters ---------------- */

const AGE_SCALE = {child: 0.66, teen: 0.88, young: 1, adult: 1, middle: 1, old: 0.96};
const BUILD_W = {thin: 0.82, normal: 1, stout: 1.3, muscular: 1.18};

export function makeCharacter(look, {mood = "平静", pose = "stand", extra = false} = {}) {
  look = look || {};
  const root = new THREE.Group();
  const body = new THREE.Group(); root.add(body);
  const w = BUILD_W[look.build] || 1;
  const skin = look.skin || "#c8a07a", top = look.top_color || "#556677", bottom = look.bottom_color || "#333333";
  const robe = ["longrobe", "gown"].includes(look.top), skirt = look.bottom === "skirt";
  const female = look.sex === "female";

  const hips = new THREE.Group(); hips.position.y = 0.85; body.add(hips);
  const legs = [];
  for (const s of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.position.set(0.085 * s * w, 0, 0); hips.add(pivot);
    const leg = mesh(new THREE.CylinderGeometry(0.06 * w, 0.05, 0.8, 6), look.bottom === "shorts" ? skin : bottom);
    leg.position.y = -0.4; pivot.add(leg);
    const shoe = mesh(new THREE.BoxGeometry(0.1, 0.06, 0.18), look.shoes_color || "#222222");
    shoe.position.set(0, -0.82, 0.04); pivot.add(shoe);
    if (look.bottom === "shorts") { const sh = mesh(new THREE.CylinderGeometry(0.075 * w, 0.07 * w, 0.3, 6), bottom); sh.position.y = -0.12; pivot.add(sh); }
    legs.push(pivot);
  }
  if (robe || skirt) {
    const len = robe ? 0.78 : 0.5;
    const cone = mesh(new THREE.CylinderGeometry(0.17 * w, 0.27 * w, len, 7), robe ? top : bottom);
    cone.position.y = -len / 2 + 0.02; hips.add(cone);
  }
  const torso = new THREE.Group(); hips.add(torso);
  const chest = mesh(new THREE.CylinderGeometry(0.2 * w, 0.16 * w, 0.62, 7), top);
  chest.position.y = 0.31; chest.scale.z = 0.72; torso.add(chest);
  if (female) { const b = mesh(new THREE.SphereGeometry(0.09 * w, 6, 4), top); b.position.set(0, 0.42, 0.07); b.scale.set(1.6, 0.8, 0.8); torso.add(b); }
  const trim = mesh(new THREE.BoxGeometry(0.03, 0.5, 0.02), look.trim_color || "#333333");
  trim.position.set(0.02, 0.33, 0.12 * w); torso.add(trim);
  if (["vest", "jacket", "coat", "uniform", "armor"].includes(look.top)) {
    const coat = mesh(new THREE.CylinderGeometry(0.215 * w, 0.2 * w, look.top === "coat" ? 0.85 : 0.5, 7), look.top === "vest" ? (look.trim_color || top) : top, look.top === "armor" ? {metalness: 0.5, roughness: 0.5} : {});
    coat.position.y = look.top === "coat" ? 0.2 : 0.3; coat.scale.z = 0.76; torso.add(coat);
  }
  const arms = [], hands = [];
  for (const s of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.position.set((0.2 * w + 0.045) * s, 0.56, 0); torso.add(pivot);
    const arm = mesh(new THREE.CylinderGeometry(0.05 * w, 0.042, 0.6, 6), look.top === "vest" ? skin : top);
    arm.position.y = -0.3; pivot.add(arm);
    const hand = mesh(new THREE.IcosahedronGeometry(0.045, 0), skin); hand.position.y = -0.63; pivot.add(hand);
    arms.push(pivot); hands.push(hand);
  }
  const neck = new THREE.Group(); neck.position.y = 0.62; torso.add(neck);
  const nk = mesh(new THREE.CylinderGeometry(0.045, 0.05, 0.08, 6), skin); nk.position.y = 0.02; neck.add(nk);
  const head = new THREE.Group(); head.position.y = 0.17; neck.add(head);
  const skull = mesh(new THREE.IcosahedronGeometry(0.13, 1), skin); skull.scale.set(0.95, 1.08, 1); head.add(skull);
  for (const s of [-1, 1]) { const ear = mesh(new THREE.SphereGeometry(0.025, 5, 4), skin); ear.position.set(0.125 * s, 0, 0); head.add(ear); }
  const nose = mesh(new THREE.ConeGeometry(0.018, 0.045, 4), shade(skin, 0.9)); nose.rotation.x = Math.PI / 2; nose.position.set(0, -0.005, 0.135); head.add(nose);

  // face: eyes, brows and mouth shapes switched by mood
  const face = {eyes: [], brows: []};
  for (const s of [-1, 1]) {
    const eye = mesh(new THREE.SphereGeometry(0.017, 6, 4), "#15120f"); eye.position.set(0.045 * s, 0.025, 0.115); head.add(eye); face.eyes.push(eye);
    const brow = mesh(new THREE.BoxGeometry(0.055, 0.012, 0.012), shade(look.hair_color || "#222222", 0.8)); brow.position.set(0.047 * s, 0.065, 0.118); head.add(brow); face.brows.push(brow);
  }
  const mouthColor = "#6b2f2a";
  face.mouths = {
    line: mesh(new THREE.BoxGeometry(0.05, 0.008, 0.01), mouthColor),
    smile: mesh(new THREE.TorusGeometry(0.03, 0.007, 4, 8, Math.PI), mouthColor),
    frown: mesh(new THREE.TorusGeometry(0.028, 0.007, 4, 8, Math.PI), mouthColor),
    open: mesh(new THREE.TorusGeometry(0.018, 0.007, 4, 10), mouthColor),
  };
  face.mouths.smile.rotation.z = Math.PI;
  for (const [k, m] of Object.entries(face.mouths)) { m.position.set(0, k === "smile" ? -0.045 : -0.065, 0.12); head.add(m); }

  // hair and hat
  const hc = look.hair_color || "#222222", hs = look.hair_style || "short";
  const cap = (theta, color, r = 0.138, open = 0) => {
    const g = open ? new THREE.SphereGeometry(r, 9, 6, Math.PI * (0.5 + open / 2), Math.PI * (2 - open), 0, theta)
                   : new THREE.SphereGeometry(r, 9, 6, 0, Math.PI * 2, 0, theta);
    return mesh(g, color);
  };
  if (hs !== "bald") {
    const top = cap(["long", "bob"].includes(hs) ? Math.PI * 0.62 : Math.PI * 0.45, hc, 0.138, ["long", "bob"].includes(hs) ? 0.55 : 0);
    top.position.y = 0.01; top.scale.y = 1.08; head.add(top);
    if (hs === "long") { const back = mesh(new THREE.BoxGeometry(0.24, 0.32, 0.06), hc); back.position.set(0, -0.12, -0.1); head.add(back); }
    if (hs === "bun") { const b = mesh(new THREE.IcosahedronGeometry(0.06, 0), hc); b.position.set(0, 0.06, -0.14); head.add(b); }
    if (hs === "topknot") { const b = mesh(new THREE.IcosahedronGeometry(0.045, 0), hc); b.position.set(0, 0.16, -0.02); head.add(b); }
    if (hs === "ponytail") { const b = mesh(new THREE.ConeGeometry(0.04, 0.25, 5), hc); b.position.set(0, -0.05, -0.15); b.rotation.x = -0.3; head.add(b); }
    if (hs === "braid") for (let k = 0; k < 5; k++) { const b = mesh(new THREE.IcosahedronGeometry(0.03, 0), hc); b.position.set(0, -0.04 - k * 0.06, -0.14); head.add(b); }
    if (hs === "messy") for (let k = 0; k < 6; k++) { const b = mesh(new THREE.ConeGeometry(0.03, 0.07, 4), hc); const a = k / 6 * Math.PI * 2; b.position.set(Math.cos(a) * 0.09, 0.12, Math.sin(a) * 0.09); b.rotation.set(Math.sin(a) * 0.6, 0, -Math.cos(a) * 0.6); head.add(b); }
  }
  const fh = look.facial_hair;
  if (fh === "mustache" || fh === "beard" || fh === "goatee") { const m = mesh(new THREE.BoxGeometry(0.07, 0.014, 0.015), hc); m.position.set(0, -0.035, 0.125); head.add(m); }
  if (fh === "beard") { const b = mesh(new THREE.ConeGeometry(0.08, 0.16, 6), hc); b.rotation.x = Math.PI; b.position.set(0, -0.14, 0.06); head.add(b); }
  if (fh === "goatee") { const b = mesh(new THREE.ConeGeometry(0.025, 0.07, 5), hc); b.rotation.x = Math.PI; b.position.set(0, -0.13, 0.1); head.add(b); }
  if (fh === "stubble") { const b = cap(Math.PI * 0.3, shade(skin, 0.75), 0.131); b.rotation.x = Math.PI; b.position.y = -0.01; head.add(b); }
  const hat = look.hat, hcol = look.hat_color || "#444444";
  if (hat === "cap" || hat === "bowler" || hat === "helmet") { const h = cap(Math.PI / 2, hcol, 0.145, 0); h.position.y = 0.03; head.add(h); if (hat === "helmet") h.material = mat(hcol, {metalness: 0.6, roughness: 0.4}); }
  if (hat === "cap") { const b = mesh(new THREE.BoxGeometry(0.16, 0.01, 0.09), hcol); b.position.set(0, 0.04, 0.16); head.add(b); }
  if (hat === "bowler" || hat === "brimmed") { const b = mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.015, 10), hcol); b.position.y = 0.06; head.add(b); }
  if (hat === "brimmed") { const t = mesh(new THREE.CylinderGeometry(0.11, 0.13, 0.12, 8), hcol); t.position.y = 0.12; head.add(t); }
  if (hat === "straw") { const s = mesh(new THREE.ConeGeometry(0.32, 0.14, 10), hcol); s.position.y = 0.14; head.add(s); }
  if (hat === "scarf" || hat === "hood") { const s = cap(Math.PI * 0.65, hcol, 0.152, 0.5); s.position.y = 0.005; head.add(s); }
  if (hat === "headband") { const s = mesh(new THREE.TorusGeometry(0.135, 0.015, 4, 12), hcol); s.rotation.x = Math.PI / 2; s.position.y = 0.06; head.add(s); }
  if (hat === "crown") { const s = mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.06, 8, 1, true), hcol, {metalness: 0.7, roughness: 0.3}); s.position.y = 0.15; head.add(s); }

  // accessories
  const acc = new Set(look.accessories || []);
  const inHand = (s, obj) => { obj.position.add(new THREE.Vector3(0, -0.65, 0)); arms[s].add(obj); };
  if (acc.has("glasses")) for (const s of [-1, 1]) { const g = mesh(new THREE.TorusGeometry(0.024, 0.004, 4, 10), "#222222"); g.position.set(0.045 * s, 0.025, 0.13); head.add(g); }
  if (acc.has("pipe")) { const p = mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.12, 4), "#3b2a1a"); p.rotation.x = Math.PI / 2.4; p.position.set(0.03, -0.075, 0.17); head.add(p); }
  if (acc.has("belt")) { const b = mesh(new THREE.CylinderGeometry(0.165 * w, 0.165 * w, 0.04, 7), look.trim_color || "#222222"); b.scale.z = 0.74; b.position.y = 0.04; torso.add(b); }
  if (acc.has("bag")) { const b = mesh(new THREE.BoxGeometry(0.16, 0.14, 0.06), "#6b5236"); b.position.set(0.24 * w, -0.02, 0.02); hips.add(b); }
  if (acc.has("sword")) { const b = mesh(new THREE.BoxGeometry(0.025, 0.7, 0.02), "#999999", {metalness: 0.6, roughness: 0.4}); b.position.set(-0.24 * w, -0.2, 0); b.rotation.z = 0.2; hips.add(b); }
  if (acc.has("jewelry")) { const j = mesh(new THREE.TorusGeometry(0.06, 0.008, 4, 10), "#d8b45a", {metalness: 0.8, roughness: 0.3}); j.rotation.x = Math.PI / 2; j.position.y = 0.58; torso.add(j); }
  if (acc.has("towel")) { const t = mesh(new THREE.BoxGeometry(0.08, 0.02, 0.3), "#e8e4da"); t.position.set(0.16 * w, 0.62, 0); torso.add(t); }
  if (acc.has("fan")) { const f = mesh(new THREE.CircleGeometry(0.12, 6, 0, Math.PI), "#e6dcc0"); f.material.side = THREE.DoubleSide; inHand(1, f); }
  if (acc.has("book")) { inHand(0, mesh(new THREE.BoxGeometry(0.12, 0.16, 0.04), "#7a3b2a")); }
  if (acc.has("basket")) { inHand(0, mesh(new THREE.CylinderGeometry(0.1, 0.08, 0.1, 7), "#a07a40")); }
  if (acc.has("staff") || acc.has("cane")) { const s = mesh(new THREE.CylinderGeometry(0.015, 0.015, acc.has("staff") ? 1.7 : 0.85, 5), "#6b4a2a"); s.position.y = acc.has("staff") ? 0.2 : -0.35; inHand(1, s); }
  if (acc.has("umbrella")) { const u = mesh(new THREE.ConeGeometry(0.5, 0.2, 8), "#5a4030"); u.position.y = 0.9; inHand(1, u); }

  const s = (AGE_SCALE[look.age] || 1) * (look.height || 1);
  root.scale.setScalar(s);
  if (look.age === "old") torso.rotation.x = 0.14;
  if (extra) root.traverse(o => { if (o.isMesh) o.castShadow = false; });

  const parts = {root, body, hips, torso, neck, head, arms, legs, face, baseTilt: torso.rotation.x, pose: "stand", t: Math.random() * 10};
  setMood(parts, mood);
  setPose(parts, pose);
  root.userData.parts = parts;
  return root;
}

export function setMood(p, mood) {
  const f = p.face;
  for (const m of Object.values(f.mouths)) m.visible = false;
  const [bl, br] = f.brows;
  bl.rotation.z = br.rotation.z = 0; bl.position.y = br.position.y = 0.065;
  for (const e of f.eyes) e.scale.set(1, 1, 1);
  p.head.rotation.x = 0;
  if (mood === "高兴") { f.mouths.smile.visible = true; for (const e of f.eyes) e.scale.y = 0.45; bl.position.y = br.position.y = 0.072; }
  else if (mood === "生气") { f.mouths.frown.visible = true; bl.rotation.z = -0.45; br.rotation.z = 0.45; bl.position.y = br.position.y = 0.058; }
  else if (mood === "难过") { f.mouths.frown.visible = true; bl.rotation.z = 0.4; br.rotation.z = -0.4; p.head.rotation.x = 0.22; }
  else if (mood === "惊讶") { f.mouths.open.visible = true; for (const e of f.eyes) e.scale.setScalar(1.45); bl.position.y = br.position.y = 0.088; }
  else f.mouths.line.visible = true;
  p.mood = mood;
}

export function setPose(p, pose) {
  const [al, ar] = p.arms, [ll, lr] = p.legs;
  for (const o of [al, ar, ll, lr]) o.rotation.set(0, 0, 0);
  al.rotation.z = -0.1; ar.rotation.z = 0.1;
  p.hips.position.y = 0.85; p.body.rotation.set(0, 0, 0); p.body.position.set(0, 0, 0);
  p.torso.rotation.x = p.baseTilt; p.neck.rotation.x = 0;
  if (pose === "sit") { ll.rotation.x = lr.rotation.x = -Math.PI / 2; p.hips.position.y = 0.47; al.rotation.x = ar.rotation.x = -0.5; }
  if (pose === "kneel") { ll.rotation.x = lr.rotation.x = 1.3; p.hips.position.y = 0.45; }
  if (pose === "lie") { p.body.rotation.x = -Math.PI / 2; p.body.position.set(0, 0.15, 0.4); }
  if (pose === "point") ar.rotation.x = -1.5;
  if (pose === "raise") { al.rotation.x = ar.rotation.x = -2.9; al.rotation.z = -0.3; ar.rotation.z = 0.3; }
  if (pose === "bow") { p.torso.rotation.x = 0.6; al.rotation.x = ar.rotation.x = -0.4; }
  if (pose === "fight") { al.rotation.x = -1.2; ar.rotation.x = -1.5; ll.rotation.x = -0.35; lr.rotation.x = 0.35; }
  if (pose === "hold") { al.rotation.x = ar.rotation.x = -1.0; al.rotation.z = 0.35; ar.rotation.z = -0.35; }
  if (pose === "cry") { al.rotation.x = ar.rotation.x = -2.5; al.rotation.z = 0.55; ar.rotation.z = -0.55; p.neck.rotation.x = 0.3; }
  p.pose = pose;
}

function animateCharacter(p, dt) {
  p.t += dt;
  if (p.pose === "walk" || p.pose === "run") {
    const k = p.pose === "run" ? 9 : 5, a = p.pose === "run" ? 0.9 : 0.45, sw = Math.sin(p.t * k) * a;
    p.legs[0].rotation.x = sw; p.legs[1].rotation.x = -sw; p.arms[0].rotation.x = -sw; p.arms[1].rotation.x = sw;
    p.body.position.y = Math.abs(Math.cos(p.t * k)) * 0.04;
  } else if (p.pose !== "lie") {
    p.torso.scale.y = 1 + Math.sin(p.t * 2) * 0.006;
  }
}

/* ---------------- places ---------------- */

function house(r, look, {w = 3, d = 2.4, h = 2.4} = {}) {
  const g = new THREE.Group(), arch = look.architecture;
  const wall = mesh(new THREE.BoxGeometry(w, h, d), look.wall || "#9a8f80"); wall.position.y = h / 2; g.add(wall);
  if (arch === "old_chinese" || arch === "rustic") {
    const roof = mesh(new THREE.ConeGeometry(Math.max(w, d) * 0.78, arch === "rustic" ? 1.3 : 0.9, 4), look.roof || "#4a4a50");
    roof.rotation.y = Math.PI / 4; roof.scale.set(w / Math.max(w, d), 1, d / Math.max(w, d)); roof.position.y = h + (arch === "rustic" ? 0.65 : 0.45); g.add(roof);
    if (arch === "old_chinese") { const eave = mesh(new THREE.BoxGeometry(w + 0.5, 0.12, d + 0.5), look.roof || "#4a4a50"); eave.position.y = h + 0.05; g.add(eave); }
  } else if (arch !== "none") {
    const top = mesh(new THREE.BoxGeometry(w + 0.2, 0.15, d + 0.2), shade(look.wall || "#9a8f80", 0.8)); top.position.y = h; g.add(top);
  }
  const door = mesh(new THREE.BoxGeometry(0.7, 1.4, 0.05), shade(look.accent || "#a04030", 0.7)); door.position.set((r() - 0.5) * (w - 1.2), 0.7, d / 2 + 0.01); g.add(door);
  for (let k = 0; k < (arch === "western" || arch === "modern" ? 3 : 1); k++) {
    const win = mesh(new THREE.BoxGeometry(0.45, 0.45, 0.05), "#2a2a30", {emissive: look.time === "night" ? "#d8a050" : "#000000", emissiveIntensity: 0.6});
    win.position.set(-w / 2 + 0.5 + k * 0.9, h * 0.62, d / 2 + 0.01); g.add(win);
  }
  return g;
}

function tree(r, color = "#4f6b3a") {
  const g = new THREE.Group();
  const t = mesh(new THREE.CylinderGeometry(0.08, 0.12, 1.2, 5), "#5a4030"); t.position.y = 0.6; g.add(t);
  const c = mesh(new THREE.IcosahedronGeometry(0.7 + r() * 0.4, 0), shade(color, 0.85 + r() * 0.3)); c.position.y = 1.6 + r() * 0.3; g.add(c);
  return g;
}

function prop(kind, r, look) {
  const a = look.accent || "#a04030";
  const g = new THREE.Group();
  const add = (m, x = 0, y = 0, z = 0) => { m.position.set(x, y, z); g.add(m); return m; };
  switch (kind) {
    case "cart": add(mesh(new THREE.BoxGeometry(0.7, 0.35, 0.8), "#3a2a20"), 0, 0.65); for (const s of [-1, 1]) { const wh = add(mesh(new THREE.TorusGeometry(0.42, 0.04, 4, 14), "#2a2a2a"), 0.42 * s, 0.45); wh.rotation.y = Math.PI / 2; } for (const s of [-1, 1]) { const sh = add(mesh(new THREE.BoxGeometry(0.04, 0.04, 1.6), "#3a2a20"), 0.3 * s, 0.6, 1.0); sh.rotation.x = 0.25; } break;
    case "table": add(mesh(new THREE.BoxGeometry(1.2, 0.06, 0.7), "#6b4a2e"), 0, 0.75); for (const x of [-0.5, 0.5]) for (const z of [-0.28, 0.28]) add(mesh(new THREE.BoxGeometry(0.06, 0.75, 0.06), "#5a3a22"), x, 0.37, z); break;
    case "chair": add(mesh(new THREE.BoxGeometry(0.45, 0.05, 0.45), "#6b4a2e"), 0, 0.45); add(mesh(new THREE.BoxGeometry(0.45, 0.5, 0.05), "#6b4a2e"), 0, 0.7, -0.2); break;
    case "bed": add(mesh(new THREE.BoxGeometry(1.1, 0.4, 2), "#6b4a2e"), 0, 0.2); add(mesh(new THREE.BoxGeometry(1, 0.12, 1.9), "#c8bfa8"), 0, 0.45); break;
    case "shelf": case "cabinet": add(mesh(new THREE.BoxGeometry(1.4, 1.8, 0.4), "#5a3a22"), 0, 0.9); for (let k = 0; k < 4; k++) add(mesh(new THREE.BoxGeometry(0.2, 0.25, 0.2), shade(a, 0.6 + r() * 0.6)), -0.45 + k * 0.3, 1.25 - (k % 2) * 0.6, 0.12); break;
    case "crate": add(mesh(new THREE.BoxGeometry(0.6, 0.6, 0.6), "#8a6a40"), 0, 0.3); break;
    case "barrel": add(mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.8, 8), "#7a5232"), 0, 0.4); break;
    case "lantern": add(mesh(new THREE.CylinderGeometry(0.02, 0.02, 1.8, 4), "#3a2a20"), 0, 0.9); add(mesh(new THREE.SphereGeometry(0.16, 8, 6), "#c83a2a", {emissive: "#ff7040", emissiveIntensity: look.time === "night" ? 1.4 : 0.3}), 0, 1.75); g.userData.light = true; break;
    case "tree": g.add(tree(r)); break;
    case "bush": add(mesh(new THREE.IcosahedronGeometry(0.4, 0), "#4f6b3a"), 0, 0.3); break;
    case "rock": add(mesh(new THREE.DodecahedronGeometry(0.4 + r() * 0.3, 0), "#7a776f"), 0, 0.25); break;
    case "well": add(mesh(new THREE.CylinderGeometry(0.5, 0.55, 0.6, 8), "#8a8478"), 0, 0.3); add(mesh(new THREE.CylinderGeometry(0.4, 0.4, 0.02, 8), "#1a2a3a"), 0, 0.61); break;
    case "fence": for (let k = 0; k < 6; k++) add(mesh(new THREE.BoxGeometry(0.08, 0.8, 0.08), "#7a5a3a"), -1.25 + k * 0.5, 0.4); add(mesh(new THREE.BoxGeometry(2.6, 0.06, 0.05), "#7a5a3a"), 0, 0.6); break;
    case "stall": add(mesh(new THREE.BoxGeometry(1.4, 0.8, 0.7), "#7a5a3a"), 0, 0.4); add(mesh(new THREE.BoxGeometry(1.6, 0.05, 1), a), 0, 1.7); for (const x of [-0.7, 0.7]) add(mesh(new THREE.BoxGeometry(0.05, 1.7, 0.05), "#5a3a22"), x, 0.85, 0.4); break;
    case "sign": add(mesh(new THREE.BoxGeometry(0.06, 1.6, 0.06), "#3a2a20"), 0, 0.8); add(mesh(new THREE.BoxGeometry(0.5, 0.9, 0.05), a), 0, 1.5, 0.05); break;
    case "stairs": for (let k = 0; k < 4; k++) add(mesh(new THREE.BoxGeometry(2, 0.18, 0.4), "#8a8478"), 0, 0.09 + k * 0.18, -k * 0.4); break;
    case "stove": add(mesh(new THREE.BoxGeometry(0.9, 0.7, 0.7), "#5a5450"), 0, 0.35); add(mesh(new THREE.CylinderGeometry(0.1, 0.1, 1, 6), "#3a3633"), 0.3, 1.1, -0.2); break;
    case "pillar": add(mesh(new THREE.CylinderGeometry(0.18, 0.2, 3, 8), a), 0, 1.5); break;
    case "statue": add(mesh(new THREE.BoxGeometry(0.8, 0.5, 0.8), "#8a8478"), 0, 0.25); add(mesh(new THREE.CylinderGeometry(0.2, 0.3, 1.2, 6), "#9a948a"), 0, 1.1); add(mesh(new THREE.IcosahedronGeometry(0.22, 0), "#9a948a"), 0, 1.9); break;
    case "boat": { const b = add(mesh(new THREE.CylinderGeometry(0.5, 0.3, 2.4, 6, 1, false, 0, Math.PI), "#6b4a2e"), 0, 0.3); b.rotation.set(0, 0, Math.PI / 2); b.rotation.order = "ZYX"; break; }
    case "tent": { const t = add(mesh(new THREE.ConeGeometry(1.2, 1.6, 4), "#8a7a5a"), 0, 0.8); t.rotation.y = Math.PI / 4; break; }
    case "fire": add(mesh(new THREE.ConeGeometry(0.25, 0.6, 5), "#ff8a30", {emissive: "#ff6020", emissiveIntensity: 1.5}), 0, 0.3); g.userData.light = true; break;
    case "flag": add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 3, 4), "#3a2a20"), 0, 1.5); add(mesh(new THREE.BoxGeometry(0.9, 0.6, 0.02), a), 0.47, 2.6); break;
    case "door": add(mesh(new THREE.BoxGeometry(1.6, 2.3, 0.3), shade(look.wall || "#9a8f80", 0.85)), 0, 1.15); add(mesh(new THREE.BoxGeometry(1.1, 1.9, 0.32), shade(a, 0.7)), 0, 0.95); break;
  }
  return g;
}

const SKY = {day: "#a8c4d4", dusk: "#d8946a", night: "#0e1626"};

export function buildPlace(look, seed = "place") {
  look = {kind: "street", architecture: "old_chinese", time: "day", weather: "clear", props: [], crowd: 0, ...(look || {})};
  const r = rng(seed);
  const g = new THREE.Group();
  const kind = look.kind, indoor = look.indoor || ["room", "hall", "shop", "teahouse"].includes(kind);
  const water = ["river", "lake", "sea"].includes(kind), green = ["field", "forest", "mountain", "garden", "village", "road", "camp"].includes(kind);
  let groundColor = look.ground || (green ? "#6f7d4f" : "#7a6e5e");
  if (kind === "desert") groundColor = look.ground || "#c8a874";
  if (kind !== "sky") { const ground = mesh(new THREE.PlaneGeometry(80, 80), groundColor); ground.rotation.x = -Math.PI / 2; ground.castShadow = false; g.add(ground); }
  const place = (obj, x, z, ry = 0) => { obj.position.set(x, 0, z); obj.rotation.y = ry; g.add(obj); return obj; };
  const slots = [];  // where props go: around the edges, never where people stand (|x| < 2.6, z > -1.5)
  if (indoor) {
    const W = kind === "hall" ? 14 : 10, D = kind === "hall" ? 10 : 7, H = kind === "hall" ? 5 : 3.4;
    const floor = mesh(new THREE.PlaneGeometry(W, D + 4), shade(look.ground || "#6b5a46", 1)); floor.rotation.x = -Math.PI / 2; floor.position.set(0, 0.01, -D / 2 + 3); g.add(floor);
    const back = mesh(new THREE.BoxGeometry(W, H, 0.2), look.wall || "#9a8f80"); back.position.set(0, H / 2, -D + 1); g.add(back);
    for (const s of [-1, 1]) { const side = mesh(new THREE.BoxGeometry(0.2, H, D + 4), shade(look.wall || "#9a8f80", 0.9)); side.position.set(s * W / 2, H / 2, -D / 2 + 3); g.add(side); }
    for (let k = 0; k < 3; k++) { const beam = mesh(new THREE.BoxGeometry(W, 0.2, 0.25), shade(look.roof || "#4a3a2a", 1)); beam.position.set(0, H - 0.1, -D + 1.5 + k * 2.6); g.add(beam); }
    const win = mesh(new THREE.BoxGeometry(1.4, 1, 0.05), look.time === "night" ? "#1a2030" : "#c8d8e0", {emissive: look.time === "night" ? "#000000" : "#b0c8d8", emissiveIntensity: 0.5});
    win.position.set(-W / 4, H * 0.55, -D + 1.12); g.add(win);
    if (kind === "hall") for (const x of [-4, 4]) for (const z of [-6, -2]) place(prop("pillar", r, look), x, z);
    for (const [x, z] of [[-3.6, -4.8], [3.6, -4.8], [-4.2, -2], [4.2, -2], [0, -5.2], [-4.2, 0.8], [4.2, 0.8]]) slots.push([x, z]);
    if (kind === "shop") look.props = ["shelf", "shelf", ...look.props];
    if (kind === "teahouse") look.props = ["table", "table", "chair", ...look.props];
  } else if (["street", "alley", "station", "village"].includes(kind)) {
    const gap = kind === "alley" ? 2.8 : 4.6;
    if (kind !== "village") {
      const road = mesh(new THREE.PlaneGeometry(gap * 2 - 1, 80), shade(groundColor, 0.85)); road.rotation.x = -Math.PI / 2; road.position.y = 0.01; road.receiveShadow = true; g.add(road);
    }
    for (const s of [-1, 1]) for (let k = 0; k < 6; k++) {
      if (kind === "village" && r() < 0.35) continue;
      const w = 2.6 + r() * 1.4, h = (look.architecture === "modern" ? 4 : 2.2) + r() * (look.architecture === "modern" ? 5 : 1);
      const hs = place(house(r, look, {w: 2.4, d: w, h}), s * (gap + 1.3 + (kind === "village" ? r() * 4 : 0)), 2 - k * (w + 0.4 + (kind === "village" ? r() * 3 : 0)), -s * Math.PI / 2);
      hs.rotation.y = -s * Math.PI / 2;
    }
    for (const [x, z] of [[-gap + 0.6, -3], [gap - 0.6, -5], [-gap + 0.6, -9], [gap - 0.6, 0.5], [-gap + 0.6, 1.2], [gap - 0.6, -12]]) slots.push([x, z]);
  } else if (["courtyard", "yard", "temple", "palace"].includes(kind)) {
    const big = kind === "temple" || kind === "palace";
    place(house(r, look, {w: big ? 10 : 7, d: 3.5, h: big ? 3.6 : 2.6}), 0, -7);
    if (big) place(prop("stairs", r, look), 0, -4.6);
    for (const s of [-1, 1]) { const h = place(house(r, look, {w: 5, d: 3, h: 2.4}), s * 6.5, -2.5); h.rotation.y = -s * Math.PI / 2; }
    const wallMat = shade(look.wall || "#9a8f80", 0.95);
    for (const s of [-1, 1]) { const w = mesh(new THREE.BoxGeometry(0.4, 2.2, 10), wallMat); w.position.set(s * 8.6, 1.1, -3); g.add(w); }
    if (big) for (const x of [-3.5, -1.2, 1.2, 3.5]) place(prop("pillar", r, look), x, -5.3);
    for (const [x, z] of [[-4.5, -4.5], [4.5, -4.5], [-4.6, -0.5], [4.6, -0.5], [-3, 2], [3.2, 2]]) slots.push([x, z]);
  } else {
    // open land: hills, trees, water, dunes, rock walls
    const far = kind === "mountain" ? 14 : 5;
    for (let k = 0; k < far; k++) {
      const h = kind === "mountain" ? 8 + r() * 10 : 3 + r() * 3;
      const m = mesh(new THREE.ConeGeometry(h * 0.9, h, 5), shade(kind === "desert" ? "#b89868" : "#5f6e4a", 0.7 + r() * 0.3));
      m.position.set((r() - 0.5) * 60, h / 2 - 0.2, -18 - r() * 20); m.castShadow = false; g.add(m);
    }
    if (water) { const wtr = mesh(new THREE.PlaneGeometry(80, 26), "#3f6a82", {roughness: 0.3, metalness: 0.2}); wtr.rotation.x = -Math.PI / 2; wtr.position.set(0, 0.03, -16); g.add(wtr); }
    const trees = kind === "forest" ? 40 : kind === "garden" ? 10 : green ? 8 : 0;
    for (let k = 0; k < trees; k++) {
      const x = (r() - 0.5) * 34, z = -2 - r() * 24;
      if (Math.abs(x) < 3 && z > -4) continue;
      place(tree(r), x, z);
    }
    if (kind === "desert") for (let k = 0; k < 10; k++) { const d = mesh(new THREE.SphereGeometry(3 + r() * 3, 8, 5), "#c8a874"); d.scale.y = 0.25; d.position.set((r() - 0.5) * 40, 0, -6 - r() * 20); g.add(d); }
    if (kind === "cave" || kind === "underground") for (let k = 0; k < 26; k++) { const a = (k / 26) * Math.PI * 1.2 + Math.PI * 0.9; const rk = mesh(new THREE.DodecahedronGeometry(1.5 + r() * 2, 0), shade(look.wall || "#4a4640", 0.7 + r() * 0.4)); rk.position.set(Math.cos(a) * 9, r() * 5, Math.sin(a) * 9 - 3); g.add(rk); }
    if (kind === "garden") { const pond = mesh(new THREE.CircleGeometry(2.2, 10), "#3f6a82"); pond.rotation.x = -Math.PI / 2; pond.position.set(-4.5, 0.02, -5); g.add(pond); }
    if (kind === "camp") for (let k = 0; k < 4; k++) place(prop("tent", r, look), -7 + k * 4.5, -7 - r() * 3);
    if (kind === "sky") for (let k = 0; k < 14; k++) { const c = mesh(new THREE.IcosahedronGeometry(1.5 + r() * 2, 0), "#eef2f6"); c.scale.y = 0.45; c.position.set((r() - 0.5) * 40, -2 + r() * 8, -8 - r() * 20); c.castShadow = false; g.add(c); }
    for (const [x, z] of [[-4, -3], [4.2, -4], [-5.5, -7], [5.5, -8], [-3.2, 1.5], [3.6, 1.8]]) slots.push([x, z]);
  }
  look.props.forEach((k, i) => { if (slots[i % slots.length]) { const [x, z] = slots[i % slots.length]; place(prop(k, r, look), x + (i >= slots.length ? 1.2 : 0), z, (r() - 0.5) * 0.8); } });
  const lights = [];
  g.traverse(o => { if (o.userData && o.userData.light) lights.push(o); });
  // background crowd
  const crowd = [];
  for (let k = 0; k < (look.crowd || 0); k++) {
    const c = makeCharacter({sex: r() < 0.5 ? "male" : "female", age: ["young", "adult", "middle", "old"][Math.floor(r() * 4)], build: "normal",
      skin: "#b89070", hair_style: ["short", "crew", "bun"][Math.floor(r() * 3)], hair_color: "#2a2a2a",
      top: "jacket", top_color: shade(["#6a6a6a", "#5a6270", "#706050", "#4a5a50"][Math.floor(r() * 4)], 1), bottom: "trousers", bottom_color: "#3a3a3a"},
      {pose: r() < 0.5 ? "walk" : "stand", extra: true});
    c.position.set((r() < 0.5 ? -1 : 1) * (2.8 + r() * 3), 0, -4 - r() * 8); c.rotation.y = r() * Math.PI * 2;
    g.add(c); crowd.push(c);
  }
  g.userData = {look, lights, crowd};
  return g;
}

/* ---------------- stage: one place + cast, camera, weather ---------------- */

export class Stage {
  constructor(canvas, {preserve = false} = {}) {
    this.renderer = new THREE.WebGLRenderer({canvas, antialias: true, preserveDrawingBuffer: preserve});
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);
    this.cast = new Map();
    this.clock = new THREE.Clock();
    this.camGoal = {pos: new THREE.Vector3(0, 1.6, 5), look: new THREE.Vector3(0, 1.2, 0)};
    this.camLook = this.camGoal.look.clone();
    this.weather = null;
    this.resize();
    this.running = false;
  }
  resize() {
    const c = this.renderer.domElement, w = c.clientWidth || c.width, h = c.clientHeight || c.height;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }
  setPlace(look, seed, {time, weather} = {}) {
    const key = JSON.stringify([seed, time, weather]);
    if (this.placeKey === key) return;
    this.placeKey = key;
    if (this.place) this.scene.remove(this.place);
    for (const l of this.lightsList || []) this.scene.remove(l);
    if (this.weather) { this.scene.remove(this.weather); this.weather = null; }
    look = {...(look || {}), ...(time ? {time} : {}), ...(weather ? {weather} : {})};
    this.place = buildPlace(look, seed); this.scene.add(this.place);
    const t = look.time || "day", sky = new THREE.Color(SKY[t]);
    this.scene.background = sky;
    const hemi = new THREE.HemisphereLight(t === "night" ? "#5a6a90" : "#e8eef4", t === "night" ? "#1a1a20" : "#6a5a48", t === "night" ? 0.55 : 1.1);
    const sun = new THREE.DirectionalLight(t === "dusk" ? "#ffb070" : t === "night" ? "#8090c0" : "#fff4e0", t === "night" ? 0.45 : t === "dusk" ? 1.6 : 2.0);
    sun.position.set(t === "dusk" ? -10 : 6, t === "dusk" ? 4 : 12, 8);
    sun.castShadow = true; sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, {left: -12, right: 12, top: 12, bottom: -12, near: 1, far: 50});
    this.lightsList = [hemi, sun];
    if (t === "night") for (const l of this.place.userData.lights.slice(0, 4)) {
      const p = new THREE.PointLight("#ffa060", 6, 9); l.getWorldPosition(p.position); p.position.y += 1.6; this.lightsList.push(p);
    }
    if (look.kind === "cave" || look.kind === "underground") { hemi.intensity = 0.35; sun.intensity = 0.4; this.scene.background = new THREE.Color("#141210"); }
    for (const l of this.lightsList) this.scene.add(l);
    const w = look.weather;
    this.scene.fog = w === "fog" ? new THREE.Fog(sky.clone().lerp(new THREE.Color("#c8c8c8"), 0.6), 4, 26)
                   : w === "dust" ? new THREE.Fog("#c8a878", 4, 30)
                   : new THREE.Fog(sky, 30, 90);
    if (w === "rain" || w === "snow") {
      const n = w === "rain" ? 1800 : 900, pos = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) { pos[i * 3] = (Math.random() - 0.5) * 30; pos[i * 3 + 1] = Math.random() * 12; pos[i * 3 + 2] = (Math.random() - 0.5) * 24 - 4; }
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      this.weather = new THREE.Points(geo, new THREE.PointsMaterial({color: w === "rain" ? "#aab8c8" : "#ffffff", size: w === "rain" ? 0.05 : 0.09, transparent: true, opacity: 0.8}));
      this.weather.userData.speed = w === "rain" ? 14 : 1.2;
      this.scene.add(this.weather);
    }
  }
  // cast: [{id, look, mood, pose, facing}] — placed left to right, facing the camera or each other
  setCast(list) {
    const want = new Set(list.map(x => x.id));
    for (const [id, c] of this.cast) if (!want.has(id)) { this.scene.remove(c); this.cast.delete(id); }
    const n = list.length, span = Math.min(3.6, 1.3 * (n - 1));
    list.forEach((x, i) => {
      let c = this.cast.get(x.id);
      const sig = JSON.stringify(x.look);
      if (!c || c.userData.sig !== sig) { if (c) this.scene.remove(c); c = makeCharacter(x.look, {mood: x.mood, pose: x.pose}); c.userData.sig = sig; this.cast.set(x.id, c); this.scene.add(c); }
      const p = c.userData.parts;
      if (x.mood && p.mood !== x.mood) setMood(p, x.mood);
      if (x.pose && p.pose !== x.pose) setPose(p, x.pose);
      c.userData.home = new THREE.Vector3(n === 1 ? 0 : -span / 2 + span * i / (n - 1), 0, x.z || 0);
      if (!c.userData.placed) { c.position.copy(c.userData.home); c.userData.placed = true; }
      c.userData.facing = x.facing || "camera";
    });
    for (const [, c] of this.cast) {
      const f = c.userData.facing, other = f !== "camera" && this.cast.get(f);
      c.userData.yaw = other ? Math.atan2(other.userData.home.x - c.userData.home.x, (other.userData.home.z - c.userData.home.z) || 0.0001) * 0.55 : 0;
    }
  }
  setMood(id, mood) { const c = this.cast.get(id); if (c && mood) setMood(c.userData.parts, mood); }
  focus(id, shot = "medium") {
    const c = id && this.cast.get(id);
    const x = c ? c.userData.home.x : 0;
    for (const [cid, o] of this.cast) o.userData.lift = cid === id ? 0.35 : 0;
    if (shot === "close" && c) {
      c.updateMatrixWorld(true);
      const y = c.userData.parts.head.getWorldPosition(new THREE.Vector3()).y;
      // the subject sits left of centre: the chat panel covers the right of the screen
      this.camGoal = {pos: new THREE.Vector3(x + 0.75, y - 0.05, 3.1), look: new THREE.Vector3(x + 0.55, y - 0.3, 0)};
    }
    else if (shot === "wide") this.camGoal = {pos: new THREE.Vector3(0, 2.6, 8.5), look: new THREE.Vector3(0, 1.1, -1)};
    else this.camGoal = {pos: new THREE.Vector3(x * 0.35, 1.7, 5.2), look: new THREE.Vector3(x * 0.3, 1.15, 0)};
  }
  snap() { this.camera.position.copy(this.camGoal.pos); this.camLook.copy(this.camGoal.look); for (const [, c] of this.cast) c.position.copy(c.userData.home); }
  frame(dt) {
    const k = 1 - Math.pow(0.02, dt);
    this.camera.position.lerp(this.camGoal.pos, k); this.camLook.lerp(this.camGoal.look, k); this.camera.lookAt(this.camLook);
    for (const [, c] of this.cast) {
      const goal = c.userData.home.clone(); goal.z += c.userData.lift || 0;
      c.position.lerp(goal, k); c.rotation.y += ((c.userData.yaw || 0) - c.rotation.y) * k;
      animateCharacter(c.userData.parts, dt);
    }
    if (this.place) for (const c of this.place.userData.crowd) animateCharacter(c.userData.parts, dt);
    if (this.weather) {
      const a = this.weather.geometry.attributes.position, v = this.weather.userData.speed * dt;
      for (let i = 1; i < a.array.length; i += 3) { a.array[i] -= v; if (a.array[i] < 0) a.array[i] += 12; }
      a.needsUpdate = true;
    }
    this.renderer.render(this.scene, this.camera);
  }
  start() {
    if (this.running) return;
    this.running = true;
    const loop = () => { if (!this.running) return; this.frame(Math.min(0.05, this.clock.getDelta())); requestAnimationFrame(loop); };
    loop();
  }
  stop() { this.running = false; }
}

// an anchor still: the place, the people in their poses, background figures
export function stillCast(still, looks, names) {
  return (still.people || []).map(x => ({id: x.id, look: looks.characters[x.id], mood: x.mood, pose: x.pose, facing: x.facing, name: names[x.id]}));
}
export function addExtras(stage, still) {
  const r = rng(JSON.stringify(still.extras || []));
  const g = new THREE.Group();
  for (const e of still.extras || []) for (let k = 0; k < e.count; k++) {
    const c = makeCharacter({sex: "male", age: "young", build: "normal", skin: "#b08868", hair_style: "crew", hair_color: "#1a1a1a", top: "uniform",
      top_color: e.color, bottom: "trousers", bottom_color: shade(e.color, 0.7), hat: "cap", hat_color: shade(e.color, 0.8)}, {pose: "stand", extra: true});
    c.position.set((k - e.count / 2) * 0.9 + (r() - 0.5) * 0.4, 0, -2.2 - r() * 1.6); c.rotation.y = (r() - 0.5) * 0.6;
    g.add(c);
  }
  for (const [i, k] of (still.props || []).entries()) { const pr = prop(k, r, {time: still.time}); pr.position.set(i % 2 ? 3.4 : -3.4, 0, -1 - i); g.add(pr); }
  stage.scene.add(g);
  return g;
}

/* ---------------- diorama map ---------------- */

const ICON = {street: "house", alley: "house", courtyard: "house", room: "house", hall: "house", shop: "house", teahouse: "house",
  yard: "house", village: "house", station: "house", temple: "house", palace: "house", forest: "tree", garden: "tree", field: "tree",
  road: "tree", mountain: "peak", river: "water", lake: "water", sea: "water", camp: "tent", cave: "peak", underground: "peak", desert: "peak", sky: "cloud"};

export class Diorama {
  constructor(canvas, labels, onPick) {
    this.renderer = new THREE.WebGLRenderer({canvas, antialias: true});
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color("#1a1c22");
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 500);
    this.scene.add(new THREE.HemisphereLight("#e8eef4", "#3a3430", 1.2));
    const sun = new THREE.DirectionalLight("#fff4e0", 1.6); sun.position.set(10, 20, 10); this.scene.add(sun);
    this.labels = labels; this.onPick = onPick; this.group = null; this.yaw = 0.6; this.tilt = 0.9; this.dist = 26;
    this.ray = new THREE.Raycaster();
    let drag = null;
    canvas.addEventListener("pointerdown", e => { drag = {x: e.clientX, y: e.clientY, moved: false}; });
    canvas.addEventListener("pointermove", e => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; this.yaw -= dx * 0.008; this.tilt = Math.min(1.4, Math.max(0.35, this.tilt + dy * 0.006)); drag.x = e.clientX; drag.y = e.clientY; });
    canvas.addEventListener("pointerup", e => { if (drag && !drag.moved) this.pick(e); drag = null; });
    canvas.addEventListener("wheel", e => { e.preventDefault(); this.dist = Math.min(80, Math.max(8, this.dist * (1 + e.deltaY * 0.001))); }, {passive: false});
  }
  pick(e) {
    const c = this.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new THREE.Vector2((e.clientX - c.left) / c.width * 2 - 1, -(e.clientY - c.top) / c.height * 2 + 1), this.camera);
    const hit = this.ray.intersectObjects(this.pads, true)[0];
    if (hit) { let o = hit.object; while (o && !o.userData.node) o = o.parent; if (o) this.onPick(o.userData.node); }
  }
  // nodes: [{name, parent}], moves: [[a,b,k]], here: node name, people: {node: [{name, look}]}
  set({nodes, moves, here, people, looks}) {
    if (this.group) this.scene.remove(this.group);
    const g = this.group = new THREE.Group(); this.scene.add(g);
    this.pads = []; this.labelItems = [];
    this.labels.innerHTML = "";
    const pos = {}, roots = nodes.filter(n => !n.parent || !nodes.find(m => m.name === n.parent));
    roots.forEach((n, k) => { const a = 2 * Math.PI * k / Math.max(1, roots.length); const R = roots.length > 1 ? 4 + roots.length * 0.9 : 0; pos[n.name] = [Math.cos(a) * R, Math.sin(a) * R]; });
    let changed = true;
    while (changed) {
      changed = false;
      for (const p of Object.keys(pos)) {
        const kids = nodes.filter(n => n.parent === p && !pos[n.name]);
        kids.forEach((n, k) => { const a = 2 * Math.PI * k / Math.max(1, kids.length) + 0.4; pos[n.name] = [pos[p][0] + Math.cos(a) * 3.2, pos[p][1] + Math.sin(a) * 3.2]; changed = true; });
      }
    }
    const link = (a, b, color, h) => {
      const A = new THREE.Vector3(pos[a][0], h, pos[a][1]), B = new THREE.Vector3(pos[b][0], h, pos[b][1]);
      const len = A.distanceTo(B), m = mesh(new THREE.BoxGeometry(0.14, 0.04, len), color);
      m.position.copy(A).lerp(B, 0.5); m.lookAt(B); g.add(m);
    };
    for (const [a, b] of moves) if (pos[a] && pos[b]) link(a, b, "#8a7a5a", 0.12);
    for (const n of nodes) if (n.parent && pos[n.parent]) link(n.name, n.parent, "#4a4d56", 0.08);
    for (const n of nodes) {
      const [x, z] = pos[n.name], lk = looks.places[n.name] || {}, isHere = n.name === here;
      const pad = new THREE.Group(); pad.userData.node = n.name; pad.position.set(x, 0, z); g.add(pad); this.pads.push(pad);
      const base = mesh(new THREE.CylinderGeometry(1.1, 1.2, 0.25, 6), isHere ? "#d8b96e" : (lk.ground || "#6f6a5e")); base.position.y = 0.12; pad.add(base);
      const icon = ICON[lk.kind] || "house";
      if (icon === "house") { const h = house(rng(n.name), {...lk, time: "day"}, {w: 1.1, d: 0.9, h: 0.7}); h.scale.setScalar(0.9); h.position.y = 0.25; pad.add(h); }
      if (icon === "tree") for (let k = 0; k < 3; k++) { const t = tree(rng(n.name + k)); t.scale.setScalar(0.35); t.position.set(Math.cos(k * 2.1) * 0.5, 0.25, Math.sin(k * 2.1) * 0.5); pad.add(t); }
      if (icon === "peak") { const p = mesh(new THREE.ConeGeometry(0.7, 1.3, 5), "#7a776f"); p.position.y = 0.9; pad.add(p); }
      if (icon === "water") { const w = mesh(new THREE.CylinderGeometry(0.8, 0.8, 0.05, 10), "#3f6a82"); w.position.y = 0.27; pad.add(w); }
      if (icon === "tent") { const t = prop("tent", rng(n.name), lk); t.scale.setScalar(0.5); t.position.y = 0.25; pad.add(t); }
      if (icon === "cloud") { const c = mesh(new THREE.IcosahedronGeometry(0.6, 0), "#eef2f6"); c.scale.y = 0.5; c.position.y = 0.9; pad.add(c); }
      const who = people[n.name] || [];
      who.slice(0, 5).forEach((p, k) => {
        const fig = makeCharacter(p.look, {extra: true}); fig.scale.multiplyScalar(0.55);
        const a = k / Math.max(1, who.length) * Math.PI * 2 + 0.3; fig.position.set(Math.cos(a) * 0.85, 0.25, Math.sin(a) * 0.85); fig.rotation.y = a + Math.PI / 2;
        pad.add(fig);
      });
      const el = document.createElement("div"); el.className = "maplabel" + (isHere ? " here" : "");
      el.textContent = n.name; el.onclick = () => this.onPick(n.name);
      if (who.length) { const s = document.createElement("small"); s.textContent = who.map(p => p.name).join("·"); el.appendChild(s); }
      this.labels.appendChild(el);
      this.labelItems.push({el, at: new THREE.Vector3(x, 1.6, z)});
    }
    // frame every visible node: centre on their middle, back off by their spread
    const ps = Object.values(pos), cx = ps.reduce((a, p) => a + p[0], 0) / ps.length, cz = ps.reduce((a, p) => a + p[1], 0) / ps.length;
    const rad = Math.max(4, ...ps.map(p => Math.hypot(p[0] - cx, p[1] - cz)));
    this.center = new THREE.Vector3(cx, 0, cz); this.dist = Math.min(80, rad * 2.6 + 6);
  }
  resize() { const c = this.renderer.domElement; this.renderer.setSize(c.clientWidth, c.clientHeight, false); this.camera.aspect = c.clientWidth / c.clientHeight; this.camera.updateProjectionMatrix(); }
  frame() {
    const c = this.center || new THREE.Vector3();
    this.camera.position.set(c.x + Math.sin(this.yaw) * Math.cos(this.tilt) * this.dist, Math.sin(this.tilt) * this.dist, c.z + Math.cos(this.yaw) * Math.cos(this.tilt) * this.dist);
    this.camera.lookAt(c);
    this.renderer.render(this.scene, this.camera);
    const r = this.renderer.domElement.getBoundingClientRect();
    for (const {el, at} of this.labelItems || []) {
      const v = at.clone().project(this.camera);
      el.style.display = v.z > 1 ? "none" : "";
      el.style.transform = `translate(${(v.x + 1) / 2 * r.width}px, ${(1 - v.y) / 2 * r.height}px) translate(-50%, -100%)`;
    }
  }
  start() { this.running = true; const loop = () => { if (!this.running) return; this.frame(); requestAnimationFrame(loop); }; this.resize(); loop(); }
  stop() { this.running = false; }
}
