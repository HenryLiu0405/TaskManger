(function(){'use strict';var n,ca=typeof Object.create=="function"?Object.create:function(a){function b(){}
b.prototype=a;return new b},p=typeof Object.defineProperties=="function"?Object.defineProperty:function(a,b,c){if(a==Array.prototype||a==Object.prototype)return a;
a[b]=c.value;return a};
function da(a){a=["object"==typeof globalThis&&globalThis,a,"object"==typeof window&&window,"object"==typeof self&&self,"object"==typeof global&&global];for(var b=0;b<a.length;++b){var c=a[b];if(c&&c.Math==Math)return c}throw Error("Cannot find global object");}
var q=da(this);function r(a,b){if(b)a:{var c=q;a=a.split(".");for(var d=0;d<a.length-1;d++){var h=a[d];if(!(h in c))break a;c=c[h]}a=a[a.length-1];d=c[a];b=b(d);b!=d&&b!=null&&p(c,a,{configurable:!0,writable:!0,value:b})}}
var t;if(typeof Object.setPrototypeOf=="function")t=Object.setPrototypeOf;else{var u;a:{var ea={a:!0},fa={};try{fa.__proto__=ea;u=fa.a;break a}catch(a){}u=!1}t=u?function(a,b){a.__proto__=b;if(a.__proto__!==b)throw new TypeError(a+" is not extensible");return a}:null}var ha=t;
function ia(a){var b=0;return function(){return b<a.length?{done:!1,value:a[b++]}:{done:!0}}}
function v(a){var b=typeof Symbol!="undefined"&&Symbol.iterator&&a[Symbol.iterator];if(b)return b.call(a);if(typeof a.length=="number")return{next:ia(a)};throw Error(String(a)+" is not an iterable or ArrayLike");}
function ja(a){if(!(a instanceof Object))throw new TypeError("Iterator result "+a+" is not an object");}
function y(){this.o=!1;this.j=null;this.m=void 0;this.g=1;this.i=this.l=0;this.D=this.h=null}
function z(a){if(a.o)throw new TypeError("Generator is already running");a.o=!0}
y.prototype.B=function(a){this.m=a};
function A(a,b){a.h={N:b,O:!0};a.g=a.l||a.i}
y.prototype.getNextAddressJsc=function(){return this.g};
y.prototype.getYieldResultJsc=function(){return this.m};
y.prototype.return=function(a){this.h={return:a};this.g=this.i};
y.prototype["return"]=y.prototype.return;y.prototype.T=function(a){this.h={C:a};this.g=this.i};
y.prototype.jumpThroughFinallyBlocks=y.prototype.T;y.prototype.u=function(a,b){this.g=b;return{value:a}};
y.prototype.yield=y.prototype.u;y.prototype.W=function(a,b){a=v(a);var c=a.next();ja(c);if(c.done)this.m=c.value,this.g=b;else return this.j=a,this.u(c.value,b)};
y.prototype.yieldAll=y.prototype.W;y.prototype.C=function(a){this.g=a};
y.prototype.jumpTo=y.prototype.C;y.prototype.G=function(){this.g=0};
y.prototype.jumpToEnd=y.prototype.G;y.prototype.I=function(a,b){this.l=a;b!=void 0&&(this.i=b)};
y.prototype.setCatchFinallyBlocks=y.prototype.I;y.prototype.V=function(a){this.l=0;this.i=a||0};
y.prototype.setFinallyBlock=y.prototype.V;y.prototype.H=function(a,b){this.g=a;this.l=b||0};
y.prototype.leaveTryBlock=y.prototype.H;y.prototype.F=function(a){this.l=a||0;a=this.h.N;this.h=null;return a};
y.prototype.enterCatchBlock=y.prototype.F;y.prototype.K=function(a,b,c){c?this.D[c]=this.h:this.D=[this.h];this.l=a||0;this.i=b||0};
y.prototype.enterFinallyBlock=y.prototype.K;y.prototype.U=function(a,b){b=this.D.splice(b||0)[0];(b=this.h=this.h||b)?b.O?this.g=this.l||this.i:b.C!=void 0&&this.i<b.C?(this.g=b.C,this.h=null):this.g=this.i:this.g=a};
y.prototype.leaveFinallyBlock=y.prototype.U;y.prototype.S=function(a){return new C(a)};
y.prototype.forIn=y.prototype.S;function C(a){this.i=a;this.g=[];for(var b in a)this.g.push(b);this.g.reverse()}

C.prototype.h=function(){for(;this.g.length>0;){var a=this.g.pop();if(a in this.i)return a}return null};

C.prototype.getNext=C.prototype.h;function ka(a){this.g=new y;this.h=a}
function la(a,b){z(a.g);var c=a.g.j;if(c)return D(a,"return"in c?c["return"]:function(d){return{value:d,done:!0}},b,a.g.return);
a.g.return(b);return E(a)}
function D(a,b,c,d){try{var h=b.call(a.g.j,c);ja(h);if(!h.done)return a.g.o=!1,h;var k=h.value}catch(f){return a.g.j=null,A(a.g,f),E(a)}a.g.j=null;d.call(a.g,k);return E(a)}
function E(a){for(;a.g.g;)try{var b=a.h(a.g);if(b)return a.g.o=!1,{value:b.value,done:!1}}catch(c){a.g.m=void 0,A(a.g,c)}a.g.o=!1;if(a.g.h){b=a.g.h;a.g.h=null;if(b.O)throw b.N;return{value:b.return,done:!0}}return{value:void 0,done:!0}};
function ma(a){this.next=function(b){z(a.g);a.g.j?b=D(a,a.g.j.next,b,a.g.B):(a.g.B(b),b=E(a));return b};
this.throw=function(b){z(a.g);a.g.j?b=D(a,a.g.j["throw"],b,a.g.B):(A(a.g,b),b=E(a));return b};
this.return=function(b){return la(a,b)};
this[Symbol.iterator]=function(){return this}};
function na(a){function b(d){return a.next(d)}
function c(d){return a.throw(d)}
return new Promise(function(d,h){function k(f){f.done?d(f.value):Promise.resolve(f.value).then(b,c).then(k,h)}
k(a.next())})}
function F(a){return na(new ma(new ka(a)))}
r("Symbol",function(a){function b(k){if(this instanceof b)throw new TypeError("Symbol is not a constructor");return new c(d+(k||"")+"_"+h++,k)}
function c(k,f){this.g=k;p(this,"description",{configurable:!0,writable:!0,value:f})}
if(a)return a;c.prototype.toString=function(){return this.g};
var d="jscomp_symbol_"+(Math.random()*1E9>>>0)+"_",h=0;return b});
r("Symbol.iterator",function(a){if(a)return a;a=Symbol("Symbol.iterator");p(Array.prototype,a,{configurable:!0,writable:!0,value:function(){return oa(ia(this))}});
return a});
function oa(a){a={next:a};a[Symbol.iterator]=function(){return this};
return a}
r("Promise",function(a){function b(f){this.h=0;this.i=void 0;this.g=[];this.o=!1;var e=this.j();try{f(e.resolve,e.reject)}catch(g){e.reject(g)}}
function c(){this.g=null}
function d(f){return f instanceof b?f:new b(function(e){e(f)})}
if(a)return a;c.prototype.h=function(f){if(this.g==null){this.g=[];var e=this;this.i(function(){e.l()})}this.g.push(f)};
var h=q.setTimeout;c.prototype.i=function(f){h(f,0)};
c.prototype.l=function(){for(;this.g&&this.g.length;){var f=this.g;this.g=[];for(var e=0;e<f.length;++e){var g=f[e];f[e]=null;try{g()}catch(l){this.j(l)}}}this.g=null};
c.prototype.j=function(f){this.i(function(){throw f;})};
b.prototype.j=function(){function f(l){return function(m){g||(g=!0,l.call(e,m))}}
var e=this,g=!1;return{resolve:f(this.G),reject:f(this.l)}};
b.prototype.G=function(f){if(f===this)this.l(new TypeError("A Promise cannot resolve to itself"));else if(f instanceof b)this.I(f);else{a:switch(typeof f){case "object":var e=f!=null;break a;case "function":e=!0;break a;default:e=!1}e?this.F(f):this.m(f)}};
b.prototype.F=function(f){var e=void 0;try{e=f.then}catch(g){this.l(g);return}typeof e=="function"?this.K(e,f):this.m(f)};
b.prototype.l=function(f){this.u(2,f)};b.prototype.m=function(f){this.u(1,f)};b.prototype.u=function(f,e){if(this.h!=0)throw Error("Cannot settle("+f+", "+e+"): Promise already settled in state"+this.h);this.h=f;this.i=e;this.h===2&&this.H();this.B()};b.prototype.H=function(){var f=this;h(function(){if(f.D()){var e=q.console;typeof e!="undefined"&&e.error(f.i)}},1)};b.prototype.D=function(){if(this.o)return!1;var f=q.CustomEvent,e=q.Event,g=q.dispatchEvent;if(typeof g==="undefined")return!0;typeof f==="function"?f=new f("unhandledrejection",{cancelable:!0}):typeof e==="function"?f=new e("unhandledrejection",{cancelable:!0}):(f=q.document.createEvent("CustomEvent"),f.initCustomEvent("unhandledrejection",!1,!0,f));f.promise=this;f.reason=this.i;return g(f)};b.prototype.B=function(){if(this.g!=null){for(var f=0;f<this.g.length;++f)k.h(this.g[f]);this.g=null}};
var k=new c;b.prototype.I=function(f){var e=this.j();f.J(e.resolve,e.reject)};b.prototype.K=function(f,e){var g=this.j();try{f.call(e,g.resolve,g.reject)}catch(l){g.reject(l)}};b.prototype.then=function(f,e){function g(x,B){return typeof x=="function"?function(aa){try{l(x(aa))}catch(ba){m(ba)}}:B}
var l,m,w=new b(function(x,B){l=x;m=B});this.J(g(f,l),g(e,m));return w};b.prototype.catch=function(f){return this.then(void 0,f)};b.prototype.J=function(f,e){function g(){switch(l.h){case 1:f(l.i);break;case 2:e(l.i);break;default:throw Error("Unexpected state: "+l.h);}}
var l=this;this.g==null?k.h(g):this.g.push(g);this.o=!0};b.resolve=d;b.reject=function(f){return new b(function(e,g){g(f)})};b.race=function(f){return new b(function(e,g){for(var l=v(f),m=l.next();!m.done;m=l.next())d(m.value).J(e,g)})};b.all=function(f){var e=v(f),g=e.next();return g.done?d([]):new b(function(l,m){function w(aa){return function(ba){x[aa]=ba;B--;B==0&&l(x)}}
var x=[],B=0;do x.push(void 0),B++,d(g.value).J(w(x.length-1),m),g=e.next();while(!g.done)})};return b});
function G(a,b){return Object.prototype.hasOwnProperty.call(a,b)}
var pa=typeof Object.assign=="function"?Object.assign:function(a,b){if(a==null)throw new TypeError("No nullish arg");a=Object(a);for(var c=1;c<arguments.length;c++){var d=arguments[c];if(d)for(var h in d)G(d,h)&&(a[h]=d[h])}return a};
r("Object.assign",function(a){return a||pa});
r("Symbol.dispose",function(a){return a?a:Symbol("Symbol.dispose")});
r("WeakMap",function(a){function b(g){this.g=(e+=Math.random()+1).toString();if(g){g=v(g);for(var l;!(l=g.next()).done;)l=l.value,this.set(l[0],l[1])}}
function c(){}
function d(g){var l=typeof g;return l==="object"&&g!==null||l==="function"}
function h(g){if(!G(g,f)){var l=new c;p(g,f,{value:l})}}
function k(g){var l=Object[g];l&&(Object[g]=function(m){if(m instanceof c)return m;Object.isExtensible(m)&&h(m);return l(m)})}
if(function(){if(!a||!Object.seal)return!1;try{var g=Object.seal({}),l=Object.seal({}),m=new a([[g,2],[l,3]]);if(m.get(g)!=2||m.get(l)!=3)return!1;m.delete(g);m.set(l,4);return!m.has(g)&&m.get(l)==4}catch(w){return!1}}())return a;
var f="$jscomp_hidden_"+Math.random();k("freeze");k("preventExtensions");k("seal");var e=0;b.prototype.set=function(g,l){if(!d(g))throw Error("Invalid WeakMap key");h(g);if(!G(g,f))throw Error("WeakMap key fail: "+g);g[f][this.g]=l;return this};
b.prototype.get=function(g){return d(g)&&G(g,f)?g[f][this.g]:void 0};
b.prototype.has=function(g){return d(g)&&G(g,f)&&G(g[f],this.g)};b.prototype.delete=function(g){return d(g)&&G(g,f)&&G(g[f],this.g)?delete g[f][this.g]:!1};
return b});
r("Map",function(a){function b(){var e={};return e.previous=e.next=e.head=e}
function c(e,g){var l=e[1];return oa(function(){if(l){for(;l.head!=e[1];)l=l.previous;for(;l.next!=l.head;)return l=l.next,{done:!1,value:g(l)};l=null}return{done:!0,value:void 0}})}

