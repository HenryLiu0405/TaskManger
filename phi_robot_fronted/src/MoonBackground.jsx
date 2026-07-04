import { useEffect } from 'react';
import * as THREE from 'three';

const MOUNT_ID = 'moon-bg';

/* ── Custom highlight shader ──
   Renders a bright white glow patch on the sphere surface
   around the mouse intersection point.
   With AdditiveBlending, it brightens the wireframe lines underneath. ── */
const highlightVertex = /* glsl */ `
  varying vec3 vWorldPos;
  void main() {
    vec4 worldPos = modelMatrix * vec4(position, 1.0);
    vWorldPos = worldPos.xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const highlightFragment = /* glsl */ `
  varying vec3 vWorldPos;
  uniform vec3 uIntersection;
  uniform float uActive;
  uniform float uRadius;
  void main() {
    if (uActive < 0.5) discard;
    float dist = length(vWorldPos - uIntersection);
    float glow = 1.0 - smoothstep(0.0, uRadius, dist);
    // Falloff: sharper near center, softer at edges
    glow = pow(glow, 1.5);
    if (glow < 0.03) discard;
    gl_FragColor = vec4(1.0, 1.0, 1.0, glow * 0.55);
  }
`;

export default function MoonBackground() {
  useEffect(() => {
    const container = document.getElementById(MOUNT_ID);
    if (!container) return;

    const w = window.innerWidth;
    const h = window.innerHeight;

    /* ── Scene ── */
    const scene = new THREE.Scene();

    /* ── Camera ── */
    const camera = new THREE.PerspectiveCamera(42, w / h, 0.1, 80);
    camera.position.set(0, 0, 6);
    camera.lookAt(0, 0, 0);

    /* ── Renderer ── */
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    container.appendChild(renderer.domElement);

    /* ── Moon group ── */
    const moonGroup = new THREE.Group();
    scene.add(moonGroup);

    /* ── Layer 1: fine wireframe ── */
    const geomFine = new THREE.SphereGeometry(2.0, 56, 56);
    const matFine = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      wireframe: true,
      transparent: true,
      opacity: 0.12,
      depthTest: true,
      depthWrite: true,
    });
    const moonFine = new THREE.Mesh(geomFine, matFine);
    moonFine.renderOrder = 0;
    moonGroup.add(moonFine);

    /* ── Layer 2: coarse wireframe (bolder grid) ── */
    const geomCoarse = new THREE.SphereGeometry(2.05, 26, 26);
    const matCoarse = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      wireframe: true,
      transparent: true,
      opacity: 0.18,
      depthTest: true,
      depthWrite: true,
    });
    const moonCoarse = new THREE.Mesh(geomCoarse, matCoarse);
    moonCoarse.renderOrder = 0;
    moonGroup.add(moonCoarse);

    /* ── Layer 3: subtle inner volume ── */
    const geomInner = new THREE.SphereGeometry(1.96, 48, 48);
    const matInner = new THREE.MeshBasicMaterial({
      color: 0x000000,
      transparent: true,
      opacity: 0.06,
      depthTest: true,
      depthWrite: true,
    });
    const moonInner = new THREE.Mesh(geomInner, matInner);
    moonInner.renderOrder = 0;
    moonGroup.add(moonInner);

    /* ── Layer 4: highlight glow (custom shader, additive) ── */
    const geomHighlight = new THREE.SphereGeometry(2.08, 64, 64);
    const matHighlight = new THREE.ShaderMaterial({
      vertexShader: highlightVertex,
      fragmentShader: highlightFragment,
      uniforms: {
        uIntersection: { value: new THREE.Vector3() },
        uActive: { value: 0.0 },
        uRadius: { value: 0.65 },
      },
      transparent: true,
      depthTest: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const moonHighlight = new THREE.Mesh(geomHighlight, matHighlight);
    moonHighlight.renderOrder = 1;
    moonGroup.add(moonHighlight);

    /* ── Raycaster ── */
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    function onMouseMove(e) {
      mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    }
    window.addEventListener('mousemove', onMouseMove, { passive: true });

    /* ── Animation loop ── */
    let frameId;
    const t0 = performance.now();

    function tick() {
      frameId = requestAnimationFrame(tick);
      const t = (performance.now() - t0) / 1000;

      /* ── Full-screen floating + rotation ── */
      moonGroup.rotation.y += 0.004;
      moonGroup.rotation.x += 0.0012;

      // X: full left-to-right sweep
      moonGroup.position.x = Math.sin(t * 0.33) * 2.2;
      // Y: full top-to-bottom sweep
      moonGroup.position.y = Math.cos(t * 0.42) * 1.4;
      // Z: far ↔ near breathing
      moonGroup.position.z = Math.sin(t * 0.28) * 1.3;

      /* ── Raycaster against wireframe layers ── */
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(
        [moonCoarse, moonFine],
        false,
      );

      if (intersects.length > 0) {
        matHighlight.uniforms.uIntersection.value.copy(
          intersects[0].point,
        );
        matHighlight.uniforms.uActive.value = 1.0;
      } else {
        matHighlight.uniforms.uActive.value = 0.0;
      }

      renderer.render(scene, camera);
    }
    tick();

    /* ── Resize ── */
    function onResize() {
      const nw = window.innerWidth;
      const nh = window.innerHeight;
      camera.aspect = nw / nh;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, nh);
    }
    window.addEventListener('resize', onResize);

    /* ── Cleanup ── */
    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('mousemove', onMouseMove);
      renderer.dispose();
      [
        geomFine, geomCoarse, geomInner, geomHighlight,
      ].forEach((g) => g.dispose());
      [
        matFine, matCoarse, matInner, matHighlight,
      ].forEach((m) => m.dispose());
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  return null;
}
