import assert from 'node:assert/strict';
import { test } from 'node:test';
import { attachDashboardTilt, TILT_MEDIA, TILT_SELECTOR } from '../src/lib/dashboard-tilt.mjs';

// DOM test doubles only; no browser package, network or API data needed.
class Target extends EventTarget {
  listeners = new Set();
  addEventListener(type, fn, options) { super.addEventListener(type, fn, options); this.listeners.add(fn); }
  removeEventListener(type, fn, options) { super.removeEventListener(type, fn, options); this.listeners.delete(fn); }
  fire(type, values = {}) { const event = new Event(type); Object.assign(event, values); this.dispatchEvent(event); }
}
class Element extends Target {
  attributes = new Map();
  values = new Map();
  style = {setProperty: (k,v) => this.values.set(k,v), removeProperty: k => this.values.delete(k)};
  rect = {left:100, top:100, width:200, height:100};
  reads = 0;
  closest() { return this.card ?? this; }
  contains(target) { return target === this || target?.card === this; }
  getBoundingClientRect() { this.reads++; return this.rect; }
  setAttribute(k,v) { this.attributes.set(k,v); }
  removeAttribute(k) { this.attributes.delete(k); }
}
function fixture() {
  const root = new Element(), card = new Element(), child = new Element(); child.card = card;
  root.contains = value => value === card || value === child;
  root.ownerDocument = new Target(); root.ownerDocument.hidden = false;
  const media = new Target(), coarse = new Target(); media.matches = true; coarse.matches = false;
  const view = new Target(), frames = new Map(); let next = 0;
  Object.assign(view, {
    Element, Node:Element,
    matchMedia: query => query === TILT_MEDIA ? media : coarse,
    requestAnimationFrame: callback => { frames.set(++next,callback); return next; },
    cancelAnimationFrame: id => frames.delete(id),
  });
  class Observer {
    static current;
    constructor(callback) { this.callback=callback; Observer.current=this; }
    observe(target,options) { this.target=target; this.options=options; }
    disconnect() { this.disconnected=true; }
  }
  view.MutationObserver=Observer;
  const dispose = attachDashboardTilt(root,view);
  const move = (values={}) => root.fire('pointermove',{target:child, pointerType:'mouse', buttons:0, clientX:300, clientY:100,...values});
  // target is a read-only Event property; define it for the delegated test event.
  root.fire = (type,values={}) => { const event=new Event(type); for(const [k,v] of Object.entries(values)) Object.defineProperty(event,k,{value:v}); root.dispatchEvent(event); };
  const flush = () => { const callbacks=[...frames.values()]; frames.clear(); callbacks.forEach(fn=>fn()); };
  return {root,card,child,media,coarse,view,frames,dispose,move,flush,observer:Observer.current};
}
test('many pointer events schedule a single frame and use the latest position', () => {
  const f=fixture(); for(let n=0;n<60;n++) f.move();
  assert.equal(f.frames.size,1); assert.equal(f.card.reads,0); f.flush();
  assert.equal(f.card.reads,1); assert.equal(f.card.values.get('--tilt-x'),'2.00deg');
  assert.equal(f.card.values.get('--tilt-y'),'2.00deg'); assert.equal(f.frames.size,0);
  assert.equal(f.card.attributes.get('data-tilt-active'),'true'); f.dispose();
});
test('extreme coordinates clamp rotation, light and shadow', () => {
  const f=fixture(); f.move({clientX:-1000,clientY:9999}); f.flush();
  assert.equal(f.card.values.get('--tilt-x'),'-2.00deg'); assert.equal(f.card.values.get('--tilt-y'),'-2.00deg');
  assert.equal(f.card.values.get('--light-x'),'0.0px'); assert.equal(f.card.values.get('--light-y'),'100.0px');
  assert.equal(f.card.values.get('--shadow-x'),'6.0px'); assert.equal(f.card.values.get('--shadow-y'),'24.0px'); f.dispose();
});
test('successive frames keep stable geometry without an idle animation loop',()=>{
  const f=fixture(); f.move(); f.flush();
  f.card.rect={left:90,top:90,width:220,height:120}; // Simulate transformed bounds.
  f.move(); f.flush(); assert.equal(f.card.reads,1); assert.equal(f.card.values.get('--tilt-x'),'2.00deg');
  assert.equal(f.frames.size,0); f.dispose();
});
for(const type of ['pointerleave','pointercancel','pointerdown','focusin']) test(`${type} resets styles and cancels the pending frame`,()=>{
  const f=fixture(); f.move(); f.flush(); f.move(); f.root.fire(type);
  assert.equal(f.frames.size,0); assert.equal(f.card.values.size,0); assert.equal(f.card.attributes.size,0); f.dispose();
});
for(const type of ['touch','pen']) test(`${type} pointer cannot activate tilt`,()=>{
  const f=fixture(); f.move({pointerType:type}); assert.equal(f.frames.size,0); f.dispose();
});
test('dragging does not activate tilt',()=>{
  const f=fixture(); f.move({buttons:1}); assert.equal(f.frames.size,0); f.dispose();
});
test('reduced motion or unavailable mouse capability cancels immediately',()=>{
  const f=fixture(); f.move(); f.flush(); f.move(); f.media.matches=false; f.media.fire('change');
  assert.equal(f.card.values.size,0); assert.equal(f.frames.size,0); f.move(); assert.equal(f.frames.size,0); f.dispose();
});
test('coarse input device disables tilt even when a mouse is attached',()=>{
  const f=fixture(); f.coarse.matches=true; f.coarse.fire('change'); f.move(); assert.equal(f.frames.size,0); f.dispose();
});
test('switching to a coarse device clears an active card',()=>{
  const f=fixture(); f.move(); f.flush(); f.coarse.matches=true; f.coarse.fire('change'); assert.equal(f.card.values.size,0); f.dispose();
});
test('moving between children of the same card preserves tilt; leaving clears it',()=>{
  const f=fixture(); f.move(); f.flush(); f.root.fire('pointerout',{relatedTarget:f.child}); assert.equal(f.card.attributes.size,1);
  f.root.fire('pointerout',{relatedTarget:null}); assert.equal(f.card.attributes.size,0); f.dispose();
});
test('zero-size or detached card cannot be animated',()=>{
  const f=fixture(); f.card.rect.width=0; f.move(); f.flush(); assert.equal(f.card.values.size,0);
  f.card.rect.width=200; f.move(); f.root.contains=()=>false; f.flush(); assert.equal(f.card.values.size,0); f.dispose();
});
for(const event of ['scroll','resize','blur']) test(`${event} clears the effect`,()=>{
  const f=fixture(); f.move(); f.flush(); f.view.fire(event); assert.equal(f.card.values.size,0); f.dispose();
});
test('hidden document cancels pending work',()=>{
  const f=fixture(); f.move(); f.root.ownerDocument.hidden=true; f.root.ownerDocument.fire('visibilitychange'); assert.equal(f.frames.size,0); f.dispose();
});
test('cleanup removes every listener, style and pending frame; stale callback is harmless',()=>{
  const f=fixture(); f.move(); f.flush(); f.move(); const stale=[...f.frames.values()][0]; f.dispose(); stale();
  assert.equal(f.frames.size,0); assert.equal(f.card.values.size,0);
  assert.equal(f.observer.disconnected,true);
  for(const target of [f.root,f.view,f.media,f.coarse,f.root.ownerDocument]) assert.equal(target.listeners.size,0);
  f.move(); assert.equal(f.frames.size,0); f.dispose();
});
test('shared selection excludes filtering and scrolling records',()=>{
  for(const name of ['filter-panel','match-list-panel','records-card','records-scroll','list-toolbar']) assert.ok(TILT_SELECTOR.includes(`:not(.${name})`));
});
test('container cards or cards containing a table stay steady',()=>{
  const f=fixture(); f.card.querySelector=()=>f.child;
  f.move();f.flush();assert.equal(f.frames.size,0);assert.equal(f.card.values.size,0);f.dispose();
});
test('new route/tab removal cancels an active frame without an idle loop',()=>{
  const f=fixture();f.move();f.flush();f.move();f.root.contains=()=>false;f.observer.callback();
  assert.equal(f.frames.size,0);assert.equal(f.card.values.size,0);
  assert.deepEqual(f.observer.options,{childList:true,subtree:true});f.dispose();
});
