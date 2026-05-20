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
function E(a){for(;a.g.g;)try{var b=a.h(a.g);if(b)return a.g.o=!1,{value:b.value,done:!1}}catch(c){a.g.m=void 0,A(a.g,c)}a.g.o=!1;if(a.g.h){b=a.g.h;a.g.h=null;if(b.O)throw b.N;return{value:b.return,done:!0}}return{value:void 0,done:!0}}
function ma(a){this.next=function(b){z(a.g);a.g.j?b=D(a,a.g.j.next,b,a.g.B):(a.g.B(b),b=E(a));return b};
this.throw=function(b){z(a.g);a.g.j?b=D(a,a.g.j["throw"],b,a.g.B):(A(a.g,b),b=E(a));return b};
this.return=function(b){return la(a,b)};
this[Symbol.iterator]=function(){return this}}
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
b.prototype.l=function(f){this.u(2,f)};
b.prototype.m=function(f){this.u(1,f)};
b.prototype.u=function(f,e){if(this.h!=0)throw Error("Cannot settle("+f+", "+e+"): Promise already settled in state"+this.h);this.h=f;this.i=e;this.h===2&&this.H();this.B()};
b.prototype.H=function(){var f=this;h(function(){if(f.D()){var e=q.console;typeof e!=="undefined"&&e.error(f.i)}},1)};
b.prototype.D=function(){if(this.o)return!1;var f=q.CustomEvent,e=q.Event,g=q.dispatchEvent;if(typeof g==="undefined")return!0;typeof f==="function"?f=new f("unhandledrejection",{cancelable:!0}):typeof e==="function"?f=new e("unhandledrejection",{cancelable:!0}):(f=q.document.createEvent("CustomEvent"),f.initCustomEvent("unhandledrejection",!1,!0,f));f.promise=this;f.reason=this.i;return g(f)};
b.prototype.B=function(){if(this.g!=null){for(var f=0;f<this.g.length;++f)k.h(this.g[f]);this.g=null}};
var k=new c;b.prototype.I=function(f){var e=this.j();f.J(e.resolve,e.reject)};
b.prototype.K=function(f,e){var g=this.j();try{f.call(e,g.resolve,g.reject)}catch(l){g.reject(l)}};
b.prototype.then=function(f,e){function g(x,B){return typeof x=="function"?function(aa){try{l(x(aa))}catch(ba){m(ba)}}:B}
var l,m,w=new b(function(x,B){l=x;m=B});
this.J(g(f,l),g(e,m));return w};
b.prototype.catch=function(f){return this.then(void 0,f)};
b.prototype.J=function(f,e){function g(){switch(l.h){case 1:f(l.i);break;case 2:e(l.i);break;default:throw Error("Unexpected state: "+l.h);}}
var l=this;this.g==null?k.h(g):this.g.push(g);this.o=!0};
b.resolve=d;b.reject=function(f){return new b(function(e,g){g(f)})};
b.race=function(f){return new b(function(e,g){for(var l=v(f),m=l.next();!m.done;m=l.next())d(m.value).J(e,g)})};
b.all=function(f){var e=v(f),g=e.next();return g.done?d([]):new b(function(l,m){function w(aa){return function(ba){x[aa]=ba;B--;B==0&&l(x)}}
var x=[],B=0;do x.push(void 0),B++,d(g.value).J(w(x.length-1),m),g=e.next();while(!g.done)})};
return b});
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
b.prototype.has=function(g){return d(g)&&G(g,f)&&G(g[f],this.g)};
b.prototype.delete=function(g){return d(g)&&G(g,f)&&G(g[f],this.g)?delete g[f][this.g]:!1};
return b});
r("Map",function(a){function b(){var e={};return e.previous=e.next=e.head=e}
function c(e,g){var l=e[1];return oa(function(){if(l){for(;l.head!=e[1];)l=l.previous;for(;l.next!=l.head;)return l=l.next,{done:!1,value:g(l)};l=null}return{done:!0,value:void 0}})}
function d(e,g){var l=g&&typeof g;l=="object"||l=="function"?k.has(g)?l=k.get(g):(l=""+ ++f,k.set(g,l)):l="p_"+g;var m=e[0][l];if(m&&G(e[0],l))for(e=0;e<m.length;e++){var w=m[e];if(g!==g&&w.key!==w.key||g===w.key)return{id:l,list:m,index:e,entry:w}}return{id:l,list:m,index:-1,entry:void 0}}
function h(e){this[0]={};this[1]=b();this.size=0;if(e){e=v(e);for(var g;!(g=e.next()).done;)g=g.value,this.set(g[0],g[1])}}
if(function(){if(!a||typeof a!="function"||!a.prototype.entries||typeof Object.seal!="function")return!1;try{var e=Object.seal({x:4}),g=new a(v([[e,"s"]]));if(g.get(e)!="s"||g.size!=1||g.get({x:4})||g.set({x:4},"t")!=g||g.size!=2)return!1;var l=g.entries(),m=l.next();if(m.done||m.value[0]!=e||m.value[1]!="s")return!1;m=l.next();return m.done||m.value[0].x!=4||m.value[1]!="t"||!l.next().done?!1:!0}catch(w){return!1}}())return a;
var k=new WeakMap;h.prototype.set=function(e,g){e=e===0?0:e;var l=d(this,e);l.list||(l.list=this[0][l.id]=[]);l.entry?l.entry.value=g:(l.entry={next:this[1],previous:this[1].previous,head:this[1],key:e,value:g},l.list.push(l.entry),this[1].previous.next=l.entry,this[1].previous=l.entry,this.size++);return this};
h.prototype.delete=function(e){e=d(this,e);return e.entry&&e.list?(e.list.splice(e.index,1),e.list.length||delete this[0][e.id],e.entry.previous.next=e.entry.next,e.entry.next.previous=e.entry.previous,e.entry.head=null,this.size--,!0):!1};
h.prototype.clear=function(){this[0]={};this[1]=this[1].previous=b();this.size=0};
h.prototype.has=function(e){return!!d(this,e).entry};
h.prototype.get=function(e){return(e=d(this,e).entry)&&e.value};
h.prototype.entries=function(){return c(this,function(e){return[e.key,e.value]})};
h.prototype.keys=function(){return c(this,function(e){return e.key})};
h.prototype.values=function(){return c(this,function(e){return e.value})};
h.prototype.forEach=function(e,g){for(var l=this.entries(),m;!(m=l.next()).done;)m=m.value,e.call(g,m[1],m[0],this)};
h.prototype[Symbol.iterator]=h.prototype.entries;var f=0;return h});
r("Set",function(a){function b(c){this.g=new Map;if(c){c=v(c);for(var d;!(d=c.next()).done;)this.add(d.value)}this.size=this.g.size}
if(function(){if(!a||typeof a!="function"||!a.prototype.entries||typeof Object.seal!="function")return!1;try{var c=Object.seal({x:4}),d=new a(v([c]));if(!d.has(c)||d.size!=1||d.add(c)!=d||d.size!=1||d.add({x:4})!=d||d.size!=2)return!1;var h=d.entries(),k=h.next();if(k.done||k.value[0]!=c||k.value[1]!=c)return!1;k=h.next();return k.done||k.value[0]==c||k.value[0].x!=4||k.value[1]!=k.value[0]?!1:h.next().done}catch(f){return!1}}())return a;
b.prototype.add=function(c){c=c===0?0:c;this.g.set(c,c);this.size=this.g.size;return this};
b.prototype.delete=function(c){c=this.g.delete(c);this.size=this.g.size;return c};
b.prototype.clear=function(){this.g.clear();this.size=0};
b.prototype.has=function(c){return this.g.has(c)};
b.prototype.entries=function(){return this.g.entries()};
b.prototype.values=function(){return this.g.values()};
b.prototype.keys=b.prototype.values;b.prototype[Symbol.iterator]=b.prototype.values;b.prototype.forEach=function(c,d){var h=this;this.g.forEach(function(k){return c.call(d,k,k,h)})};
return b});
r("Array.prototype.find",function(a){return a?a:function(b,c){a:{var d=this;d instanceof String&&(d=String(d));for(var h=d.length,k=0;k<h;k++){var f=d[k];if(b.call(c,f,k,d)){b=f;break a}}b=void 0}return b}});
r("Array.from",function(a){return a?a:function(b,c,d){c=c!=null?c:function(e){return e};
var h=[],k=typeof Symbol!="undefined"&&Symbol.iterator&&b[Symbol.iterator];if(typeof k=="function"){b=k.call(b);for(var f=0;!(k=b.next()).done;)h.push(c.call(d,k.value,f++))}else for(k=b.length,f=0;f<k;f++)h.push(c.call(d,b[f],f));return h}});/*

 Copyright The Closure Library Authors.
 SPDX-License-Identifier: Apache-2.0
*/
var H=this||self;function I(a){var b=typeof a;return b=="object"&&a!=null||b=="function"}
function qa(a){return Object.prototype.hasOwnProperty.call(a,ra)&&a[ra]||(a[ra]=++sa)}
var ra="closure_uid_"+(Math.random()*1E9>>>0),sa=0;function J(a,b){a=a.split(".");for(var c=H,d;a.length&&(d=a.shift());)a.length||b===void 0?c[d]&&c[d]!==Object.prototype[d]?c=c[d]:c=c[d]={}:c[d]=b}
function ta(a,b){function c(){}
c.prototype=b.prototype;a.R=b.prototype;a.prototype=new c;a.prototype.constructor=a;a.ea=function(d,h,k){for(var f=Array(arguments.length-2),e=2;e<arguments.length;e++)f[e-2]=arguments[e];return b.prototype[h].apply(d,f)}}
;var ua=Array.prototype.indexOf?function(a,b){return Array.prototype.indexOf.call(a,b,void 0)}:function(a,b){if(typeof a==="string")return typeof b!=="string"||b.length!=1?-1:a.indexOf(b,0);
for(var c=0;c<a.length;c++)if(c in a&&a[c]===b)return c;return-1},va=Array.prototype.forEach?function(a,b,c){Array.prototype.forEach.call(a,b,c)}:function(a,b,c){for(var d=a.length,h=typeof a==="string"?a.split(""):a,k=0;k<d;k++)k in h&&b.call(c,h[k],k,a)};
function wa(a,b){b=ua(a,b);b>=0&&Array.prototype.splice.call(a,b,1)}
function xa(a){return Array.prototype.concat.apply([],arguments)}
function ya(a){var b=a.length;if(b>0){for(var c=Array(b),d=0;d<b;d++)c[d]=a[d];return c}return[]}
;function za(a,b){this.i=a;this.j=b;this.h=0;this.g=null}
za.prototype.get=function(){if(this.h>0){this.h--;var a=this.g;this.g=a.next;a.next=null}else a=this.i();return a};function Aa(a){H.setTimeout(function(){throw a;},0)}
;function Ba(){this.h=this.g=null}
Ba.prototype.add=function(a,b){var c=Ca.get();c.set(a,b);this.h?this.h.next=c:this.g=c;this.h=c};
Ba.prototype.remove=function(){var a=null;this.g&&(a=this.g,this.g=this.g.next,this.g||(this.h=null),a.next=null);return a};
var Ca=new za(function(){return new Da},function(a){return a.reset()});
function Da(){this.next=this.scope=this.g=null}
Da.prototype.set=function(a,b){this.g=a;this.scope=b;this.next=null};
Da.prototype.reset=function(){this.next=this.scope=this.g=null};var Ea,Fa=!1,Ga=new Ba;function Ha(a){Ea||Ia();Fa||(Ea(),Fa=!0);Ga.add(a,void 0)}
function Ia(){var a=Promise.resolve(void 0);Ea=function(){a.then(Ja)}}
function Ja(){for(var a;a=Ga.remove();){try{a.g.call(a.scope)}catch(c){Aa(c)}var b=Ca;b.j(a);b.h<100&&(b.h++,a.next=b.g,b.g=a)}Fa=!1}
;function K(){this.i=this.i;this.j=this.j}
K.prototype.i=!1;K.prototype.dispose=function(){this.i||(this.i=!0,this.L())};
K.prototype[Symbol.dispose]=function(){this.dispose()};
K.prototype.addOnDisposeCallback=function(a,b){this.i?b!==void 0?a.call(b):a():(this.j||(this.j=[]),b&&(a=a.bind(b)),this.j.push(a))};
K.prototype.L=function(){if(this.j)for(;this.j.length;)this.j.shift()()};function Ka(a){var b={},c;for(c in a)b[c]=a[c];return b}
;var La=/&/g,Ma=/</g,Na=/>/g,Oa=/"/g,Pa=/'/g,Qa=/\x00/g,Ra=/[\x00&<>"']/;/*

 Copyright Google LLC
 SPDX-License-Identifier: Apache-2.0
*/
function L(a){this.g=a}
L.prototype.toString=function(){return this.g};
var Sa=new L("about:invalid#zClosurez");function Ta(a){this.Y=a}
function M(a){return new Ta(function(b){return b.substr(0,a.length+1).toLowerCase()===a+":"})}
var Ua=[M("data"),M("http"),M("https"),M("mailto"),M("ftp"),new Ta(function(a){return/^[^:]*([/?#]|$)/.test(a)})],Va=/^\s*(?!javascript:)(?:[\w+.-]+:|[^:/?#]*(?:[/?#]|$))/i;var Wa={da:0,ba:1,ca:2,0:"FORMATTED_HTML_CONTENT",1:"EMBEDDED_INTERNAL_CONTENT",2:"EMBEDDED_TRUSTED_EXTERNAL_CONTENT"};function N(a,b){b=Error.call(this,a+" cannot be used with intent "+Wa[b]);this.message=b.message;"stack"in b&&(this.stack=b.stack);this.type=a;this.name="TypeCannotBeUsedWithIframeIntentError"}
var O=Error;N.prototype=ca(O.prototype);N.prototype.constructor=N;if(ha)ha(N,O);else for(var P in O)if(P!="prototype")if(Object.defineProperties){var Xa=Object.getOwnPropertyDescriptor(O,P);Xa&&Object.defineProperty(N,P,Xa)}else N[P]=O[P];N.R=O.prototype;function Ya(a){Ra.test(a)&&(a.indexOf("&")!=-1&&(a=a.replace(La,"&amp;")),a.indexOf("<")!=-1&&(a=a.replace(Ma,"&lt;")),a.indexOf(">")!=-1&&(a=a.replace(Na,"&gt;")),a.indexOf('"')!=-1&&(a=a.replace(Oa,"&quot;")),a.indexOf("'")!=-1&&(a=a.replace(Pa,"&#39;")),a.indexOf("\x00")!=-1&&(a=a.replace(Qa,"&#0;")));return a}
;var Za,Q;a:{for(var $a=["CLOSURE_FLAGS"],R=H,ab=0;ab<$a.length;ab++)if(R=R[$a[ab]],R==null){Q=null;break a}Q=R}var bb=Q&&Q[610401301];Za=bb!=null?bb:!1;function S(){var a=H.navigator;return a&&(a=a.userAgent)?a:""}
var T,cb=H.navigator;T=cb?cb.userAgentData||null:null;function db(){return Za?!!T&&T.brands.length>0:!1}
function eb(a){var b={};a.forEach(function(c){b[c[0]]=c[1]});
return function(c){return b[c.find(function(d){return d in b})]||""}}
function fb(){for(var a=S(),b=RegExp("([A-Z][\\w ]+)/([^\\s]+)\\s*(?:\\((.*?)\\))?","g"),c=[],d;d=b.exec(a);)c.push([d[1],d[2],d[3]||void 0]);a=eb(c);if(db())a:{if(Za&&T)for(b=0;b<T.brands.length;b++)if((c=T.brands[b].brand)&&c.indexOf("Chromium")!=-1){b=!0;break a}b=!1}else b=(S().indexOf("Chrome")!=-1||S().indexOf("CriOS")!=-1)&&(db()||S().indexOf("Edge")==-1)||S().indexOf("Silk")!=-1;return b?a(["Chrome","CriOS","HeadlessChrome"]):""}
function gb(){if(db()){var a=T.brands.find(function(b){return b.brand==="Chromium"});
if(!a||!a.version)return NaN;a=a.version.split(".")}else{a=fb();if(a==="")return NaN;a=a.split(".")}return a.length===0?NaN:Number(a[0])}
;function U(a){K.call(this);this.o=1;this.l=[];this.m=0;this.g=[];this.h={};this.u=!!a}
ta(U,K);n=U.prototype;n.subscribe=function(a,b,c){var d=this.h[a];d||(d=this.h[a]=[]);var h=this.o;this.g[h]=a;this.g[h+1]=b;this.g[h+2]=c;this.o=h+3;d.push(h);return h};
n.unsubscribe=function(a,b,c){if(a=this.h[a]){var d=this.g;if(a=a.find(function(h){return d[h+1]==b&&d[h+2]==c}))return this.M(a)}return!1};
n.M=function(a){var b=this.g[a];if(b){var c=this.h[b];this.m!=0?(this.l.push(a),this.g[a+1]=function(){}):(c&&wa(c,a),delete this.g[a],delete this.g[a+1],delete this.g[a+2])}return!!b};
n.P=function(a,b){var c=this.h[a];if(c){var d=Array(arguments.length-1),h=arguments.length,k;for(k=1;k<h;k++)d[k-1]=arguments[k];if(this.u)for(k=0;k<c.length;k++)h=c[k],hb(this.g[h+1],this.g[h+2],d);else{this.m++;try{for(k=0,h=c.length;k<h&&!this.i;k++){var f=c[k];this.g[f+1].apply(this.g[f+2],d)}}finally{if(this.m--,this.l.length>0&&this.m==0)for(;c=this.l.pop();)this.M(c)}}return k!=0}return!1};
function hb(a,b,c){Ha(function(){a.apply(b,c)})}
n.clear=function(a){if(a){var b=this.h[a];b&&(b.forEach(this.M,this),delete this.h[a])}else this.g.length=0,this.h={}};
n.L=function(){U.R.L.call(this);this.clear();this.l.length=0};var ib=RegExp("^(?:([^:/?#.]+):)?(?://(?:([^\\\\/?#]*)@)?([^\\\\/?#]*?)(?::([0-9]+))?(?=[\\\\/?#]|$))?([^?#]+)?(?:\\?([^#]*))?(?:#([\\s\\S]*))?$");function jb(a){var b=a.match(ib);a=b[1];var c=b[2],d=b[3];b=b[4];var h="";a&&(h+=a+":");d&&(h+="//",c&&(h+=c+"@"),h+=d,b&&(h+=":"+b));return h}
function kb(a,b,c){if(Array.isArray(b))for(var d=0;d<b.length;d++)kb(a,String(b[d]),c);else b!=null&&c.push(a+(b===""?"":"="+encodeURIComponent(String(b))))}
var lb=/#|$/;var mb=["https://www.google.com"];function nb(){var a=this;this.g=[];this.h=function(){Promise.all(a.g.map(function(b){document.requestStorageAccessFor(b)})).then(function(){window.removeEventListener("click",a.h)})}}
function ob(){return F(function(a){var b=a.return;var c=gb()>=119;return b.call(a,c&&!!navigator.permissions&&!!navigator.permissions.query&&"requestStorageAccessFor"in document)})}
function pb(){var a=new nb,b=["https://www.youtube.com"];b=b===void 0?mb:b;F(function(c){switch(c.g){case 1:return c.u(ob(),2);case 2:if(!c.m){c.C(3);break}return c.u(Promise.all(b.map(function(d){var h;return F(function(k){if(k.g==1)return k.I(2),k.u(navigator.permissions.query({name:"top-level-storage-access",requestedOrigin:d}),4);if(k.g!=2)return h=k.m,h.state==="prompt"&&a.g.push(d),k.H(0);k.F();k.G()})})),4);
case 4:a.g.length>0&&window.addEventListener("click",a.h);case 3:return c.return()}})}
;var V={},qb=[],W=new U,rb={};function sb(){for(var a=v(qb),b=a.next();!b.done;b=a.next())b=b.value,b()}
function tb(a,b){return a.tagName.toLowerCase().substring(0,3)==="yt:"?a.getAttribute(b):a.dataset?a.dataset[b]:a.getAttribute("data-"+b)}
function ub(a){W.P.apply(W,arguments)}
;function vb(a){return(a.search("cue")===0||a.search("load")===0)&&a!=="loadModule"}
function wb(a){return a.search("get")===0||a.search("is")===0}
;var xb=window;
function X(a,b){this.A={};this.playerInfo={};this.videoTitle="";this.j=this.g=null;this.h=0;this.m=!1;this.l=[];this.i=null;this.B={};this.options=null;this.u=this.Z.bind(this);if(!a)throw Error("YouTube player element ID required.");this.id=qa(this);b=Object.assign({title:"video player",videoId:"",width:640,height:360},b||{});var c=document;if(a=typeof a==="string"?c.getElementById(a):a){xb.yt_embedsEnableRsaforFromIframeApi&&pb();c=a.tagName.toLowerCase()==="iframe";b.host||(b.host=c?jb(a.src):
"https://www.youtube.com");this.options=b||{};b=[this.options,window.YTConfig||{}];for(var d=0;d<b.length;d++)b[d].host&&(b[d].host=b[d].host.toString().replace("http://","https://"));if(!c){b=document.createElement("iframe");c=a.attributes;d=0;for(var h=c.length;d<h;d++){var k=c[d].value;k!=null&&k!==""&&k!=="null"&&b.setAttribute(c[d].name,k)}b.setAttribute("frameBorder","0");b.setAttribute("allowfullscreen","");b.setAttribute("allow","accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share");
b.setAttribute("referrerPolicy","strict-origin-when-cross-origin");b.setAttribute("title","YouTube "+Y(this,"title"));(c=Y(this,"width"))&&b.setAttribute("width",c.toString());(c=Y(this,"height"))&&b.setAttribute("height",c.toString());this.j=a;(c=a.parentNode)&&c.replaceChild(b,a);a=yb(this,b);c=""+Y(this,"host")+zb(this)+"?";d=[];for(var f in a)kb(f,a[f],d);f=c+d.join("&");if(xb.yt_embedsEnableIframeSrcWithIntent){var e=e===void 0?Ua:e;a:if(e=e===void 0?Ua:e,f instanceof L)e=f;else{for(a=0;a<e.length;++a)if(c=
e[a],c instanceof Ta&&c.Y(f)){e=new L(f);break a}e=void 0}e=e||Sa;b.removeAttribute("srcdoc");f="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-storage-access-by-user-activation".split(" ");b.setAttribute("sandbox","");for(a=0;a<f.length;a++)b.sandbox.supports&&!b.sandbox.supports(f[a])||b.sandbox.add(f[a]);if(e instanceof L)if(e instanceof L)e=e.g;else throw Error("");else e=Va.test(e)?e:void 0;e!==void 0&&(b.src=e);b.sandbox.add("allow-presentation",
"allow-top-navigation")}else b.src=f;a=b}this.g=a;this.g.id||(this.g.id="widget"+qa(this.g));V[this.g.id]=this;if(window.postMessage){this.i=new U;Ab(this);b=Y(this,"events");for(var g in b)b.hasOwnProperty(g)&&this.addEventListener(g,b[g]);for(var l in rb)rb.hasOwnProperty(l)&&Bb(this,l)}}}
n=X.prototype;n.setSize=function(a,b){this.g.width=a.toString();this.g.height=b.toString();return this};
n.getIframe=function(){return this.g};
n.addEventListener=function(a,b){var c=b;typeof b==="string"&&(c=function(){window[b].apply(window,arguments)});
if(!c)return this;this.i.subscribe(a,c);Cb(this,a);return this};
function Bb(a,b){b=b.split(".");if(b.length===2){var c=b[1];"player"===b[0]&&Cb(a,c)}}
n.destroy=function(){this.g&&this.g.id&&(V[this.g.id]=null);var a=this.i;a&&typeof a.dispose=="function"&&a.dispose();if(this.j){a=this.j;var b=this.g,c=b.parentNode;c&&c.replaceChild(a,b)}else(a=this.g)&&a.parentNode&&a.parentNode.removeChild(a);Z&&(Z[this.id]=null);this.options=null;this.g&&this.o&&this.g.removeEventListener("load",this.o);this.j=this.g=null};
function Db(a,b,c){c=c||[];c=Array.prototype.slice.call(c);b={event:"command",func:b,args:c};a.m?a.sendMessage(b):a.l.push(b)}
n.Z=function(){Eb(this)||clearInterval(this.h)};
function Eb(a){if(!a.g||!a.g.contentWindow)return!1;a.sendMessage({event:"listening"});return!0}
function Ab(a){Fb(a,a.id,String(Y(a,"host")));var b=Number(xb.yt_embedsWidgetPollIntervalMs)||250;a.h=setInterval(a.u,b);a.g&&(a.o=function(){clearInterval(a.h);a.h=setInterval(a.u,b)},a.g.addEventListener("load",a.o))}
function Gb(a){var b=a.getBoundingClientRect();a=Math.max(0,Math.min(b.bottom,window.innerHeight||document.documentElement.clientHeight)-Math.max(b.top,0))*Math.max(0,Math.min(b.right,window.innerWidth||document.documentElement.clientWidth)-Math.max(b.left,0));a=(b=b.height*b.width)?a/b:0;return document.visibilityState==="hidden"||a<.5?1:a<.75?2:a<.85?3:a<.95?4:a<1?5:6}
function Cb(a,b){a.B[b]||(a.B[b]=!0,Db(a,"addEventListener",[b]))}
n.sendMessage=function(a){a.id=this.id;a.channel="widget";a=JSON.stringify(a);var b=jb(this.g.src||"").replace("http:","https:");if(this.g.contentWindow)try{this.g.contentWindow.postMessage(a,b)}catch(c){if(c.name&&c.name==="SyntaxError")c.message&&c.message.indexOf("target origin ''")>0||console&&console.warn&&console.warn(c);else throw c;}else console&&console.warn&&console.warn("The YouTube player is not attached to the DOM. API calls should be made after the onReady event. See more: https://developers.google.com/youtube/iframe_api_reference#Events")};
function zb(a){if((a=String(Y(a,"videoId")))&&(a.length!==11||!a.match(/^[a-zA-Z0-9\-_]+$/)))throw Error("Invalid video id");return"/embed/"+a}
function yb(a,b){var c=Y(a,"playerVars");c?c=Ka(c):c={};window!==window.top&&document.referrer&&(c.widget_referrer=document.referrer.substring(0,256));var d=Y(a,"embedConfig");if(d){if(I(d))try{d=JSON.stringify(d)}catch(h){console.error("Invalid embed config JSON",h)}c.embed_config=d}c.enablejsapi=window.postMessage?1:0;window.location.host&&(c.origin=window.location.protocol+"//"+window.location.host);c.widgetid=a.id;window.location.href&&va(["debugjs","debugcss"],function(h){var k=window.location.href;
var f=k.search(lb);b:{var e=0;for(var g=h.length;(e=k.indexOf(h,e))>=0&&e<f;){var l=k.charCodeAt(e-1);if(l==38||l==63)if(l=k.charCodeAt(e+g),!l||l==61||l==38||l==35)break b;e+=g+1}e=-1}if(e<0)k=null;else{g=k.indexOf("&",e);if(g<0||g>f)g=f;e+=h.length+1;k=decodeURIComponent(k.slice(e,g!==-1?g:0).replace(/\+/g," "))}k!==null&&(c[h]=k)});
window.location.href&&(c.forigin=window.location.href);a=window.location.ancestorOrigins;c.aoriginsup=a===void 0?0:1;a&&a.length>0&&(c.aorigins=Array.from(a).join(","));window.document.referrer&&(c.gporigin=window.document.referrer);b&&(c.vf=Gb(b));return c}
function Hb(a,b){if(I(b)){for(var c in b)b.hasOwnProperty(c)&&(a.playerInfo[c]=b[c]);a.playerInfo.hasOwnProperty("videoData")&&(b=a.playerInfo.videoData,b.hasOwnProperty("title")&&b.title?(b=b.title,b!==a.videoTitle&&(a.videoTitle=b,a.g.setAttribute("title",b))):(a.videoTitle="",a.g.setAttribute("title","YouTube "+Y(a,"title"))))}}
function Ib(a,b){b=v(b);for(var c=b.next(),d={};!c.done;d={v:void 0},c=b.next())d.v=c.value,a[d.v]||(d.v==="getCurrentTime"?a[d.v]=function(){var h=this.playerInfo.currentTime;if(this.playerInfo.playerState===1){var k=(Date.now()/1E3-this.playerInfo.currentTimeLastUpdated_)*this.playerInfo.playbackRate;k>0&&(h+=Math.min(k,1))}return h}:vb(d.v)?a[d.v]=function(h){return function(){this.playerInfo={};
this.A={};Db(this,h.v,arguments);return this}}(d):wb(d.v)?a[d.v]=function(h){return function(){var k=h.v,f=0;
k.search("get")===0?f=3:k.search("is")===0&&(f=2);return this.playerInfo[k.charAt(f).toLowerCase()+k.substring(f+1)]}}(d):a[d.v]=function(h){return function(){Db(this,h.v,arguments);
return this}}(d))}
n.getVideoEmbedCode=function(){var a=""+Y(this,"host")+zb(this),b=Number(Y(this,"width")),c=Number(Y(this,"height"));if(isNaN(b)||isNaN(c))throw Error("Invalid width or height property");b=Math.floor(b);c=Math.floor(c);var d=this.videoTitle;a=Ya(a);d=Ya(d!=null?d:"YouTube video player");return'<iframe width="'+b+'" height="'+c+'" src="'+a+'" title="'+(d+'" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>')};
n.getOptions=function(a){return this.A.namespaces?a?this.A[a]?this.A[a].options||[]:[]:this.A.namespaces||[]:[]};
n.getOption=function(a,b){if(this.A.namespaces&&a&&b&&this.A[a])return this.A[a][b]};
function Y(a,b){a=[a.options,window.YTConfig||{}];for(var c=0;c<a.length;c++){var d=a[c][b];if(d!==void 0)return d}return null}
var Z=null,Jb=null;function Kb(a){if(a.tagName.toLowerCase()!=="iframe"){var b=tb(a,"videoid");b&&(b={videoId:b,width:tb(a,"width"),height:tb(a,"height")},new X(a,b))}}
function Fb(a,b,c){Z||(Z={},Jb=new Set,Lb.addEventListener("message",function(d){a:if(Jb.has(d.origin)){try{var h=JSON.parse(d.data)}catch(e){break a}var k=Z[h.id];if(k&&d.origin===k.X)switch(d=k.aa,d.m=!0,d.m&&(va(d.l,d.sendMessage,d),d.l.length=0),k=h.event,h=h.info,k){case "apiInfoDelivery":if(I(h))for(var f in h)h.hasOwnProperty(f)&&(d.A[f]=h[f]);break;case "infoDelivery":Hb(d,h);break;case "initialDelivery":I(h)&&(clearInterval(d.h),d.playerInfo={},d.A={},Ib(d,h.apiInterface),Hb(d,h));break;
case "alreadyInitialized":clearInterval(d.h);break;case "readyToListen":Eb(d);break;default:d.i.i||(f={target:d,data:h},d.i.P(k,f),ub("player."+k,f))}}}));
Z[b]={aa:a,X:c};Jb.add(c)}
var Lb=window;J("YT.PlayerState.UNSTARTED",-1);J("YT.PlayerState.ENDED",0);J("YT.PlayerState.PLAYING",1);J("YT.PlayerState.PAUSED",2);J("YT.PlayerState.BUFFERING",3);J("YT.PlayerState.CUED",5);J("YT.get",function(a){return V[a]});
J("YT.scan",sb);J("YT.subscribe",function(a,b,c){W.subscribe(a,b,c);rb[a]=!0;for(var d in V)V.hasOwnProperty(d)&&Bb(V[d],a)});
J("YT.unsubscribe",function(a,b,c){W.unsubscribe(a,b,c)});
J("YT.Player",X);X.prototype.destroy=X.prototype.destroy;X.prototype.setSize=X.prototype.setSize;X.prototype.getIframe=X.prototype.getIframe;X.prototype.addEventListener=X.prototype.addEventListener;X.prototype.getVideoEmbedCode=X.prototype.getVideoEmbedCode;X.prototype.getOptions=X.prototype.getOptions;X.prototype.getOption=X.prototype.getOption;qb.push(function(a){var b=a;b||(b=document);a=ya(b.getElementsByTagName("yt:player"));b=ya((b||document).querySelectorAll(".yt-player"));va(xa(a,b),Kb)});
typeof YTConfig!=="undefined"&&YTConfig.parsetags&&YTConfig.parsetags!=="onload"||sb();var Mb=H.onYTReady;Mb&&Mb();var Nb=H.onYouTubeIframeAPIReady;Nb&&Nb();var Ob=H.onYouTubePlayerAPIReady;Ob&&Ob();}).call(this);
