// Stand-ins for the parts of @microsoft/msfs-sdk that src/Settings.tsx uses,
// enough to test its model and settings in Node (test/settings.test.js).

class Subject {
  static create(v) {
    return new Subject(v);
  }
  constructor(v) {
    this.v = v;
    this.handlers = [];
  }
  get() {
    return this.v;
  }
  set(v) {
    if (v === this.v) return;
    this.v = v;
    this.handlers.slice().forEach((h) => h(v));
  }
  sub(h, initial) {
    this.handlers.push(h);
    if (initial) h(this.v);
    return { destroy: () => (this.handlers = this.handlers.filter((x) => x !== h)) };
  }
  map(f) {
    return MappedSubject.create(([v]) => f(v), this);
  }
}

class MappedSubject extends Subject {
  static create(f, ...inputs) {
    const m = new MappedSubject(f(inputs.map((i) => i.get())));
    inputs.forEach((i) => i.sub(() => m.set(f(inputs.map((x) => x.get())))));
    return m;
  }
}

class DisplayComponent {
  constructor(props) {
    this.props = props;
  }
  onAfterRender() {}
  destroy() {}
}

const FSComponent = { buildComponent: () => ({}), Fragment: () => ({}) };

module.exports = { Subject, MappedSubject, DisplayComponent, FSComponent };
