// Existing browser-native Study proof callbacks, serialized by Playwright at unchanged call sites.
export const readStudyIntentRecords = () => new Promise((resolve,reject) => {
    const opened=indexedDB.open('tempo-study-intents'); opened.onerror=()=>reject(opened.error);
    opened.onsuccess=()=>{const db=opened.result;const read=db.transaction('intents').objectStore('intents').getAll();read.onsuccess=()=>{resolve(read.result);db.close();};read.onerror=()=>reject(read.error);};
  });

export const denyStudyIntentStorage = () => { Object.defineProperty(window,'indexedDB',{get(){throw new DOMException('Storage denied','SecurityError');}}); };

export const seedLegacyStudyIntent = () => {
    const opening=indexedDB.open('tempo-study-intents',1);
    opening.onupgradeneeded=()=>{const store=opening.result.createObjectStore('intents',{keyPath:['workspace','key']});store.add({workspace:'http://old-learning.invalid:study',key:'review:old',state:'pending',endpoint:'/api/cards/original/review',payload:{outcome:'correct'}});};
    opening.onsuccess=()=>opening.result.close();
  };

export const failReviewIntentStorage = mode => {
    const add = IDBObjectStore.prototype.add;
    IDBObjectStore.prototype.add = function(value,...keys) {
      if (this.name==='intents' && value.kind==='review') {
        if (mode==='quota') throw new DOMException('Study storage quota exhausted','QuotaExceededError');
        const request = add.call(this,value,...keys);
        request.addEventListener('success',()=>this.transaction.abort()); return request;
      }
      return add.call(this,value,...keys);
    };
  };

export const seedIncompatibleStudyIntentStorage = () => {
    const opening=indexedDB.open('tempo-study-intents',2);
    opening.onupgradeneeded=()=>opening.result.createObjectStore('intents',{keyPath:['workspace','key']});
    opening.onsuccess=()=>opening.result.close();
  };

export const readPreparedCardCount = async()=>{
        const registry=JSON.parse(localStorage.getItem('tempo-learning-authorities-v1'));
        const databaseName=Object.values(registry)[0].offlineDatabase;
        return new Promise(resolve=>{const opening=indexedDB.open(databaseName);opening.onsuccess=()=>{const db=opening.result;const read=db.transaction('training').objectStore('training').get('prepared-daily-queue');read.onsuccess=()=>{resolve(read.result?.cards?.length??0);db.close();};};});
      };

export const readPreparedAttemptCounts = async()=>{
        const registry=JSON.parse(localStorage.getItem('tempo-learning-authorities-v1'));
        return new Promise(resolve=>{const opening=indexedDB.open(Object.values(registry)[0].offlineDatabase);opening.onsuccess=()=>{const db=opening.result;const read=db.transaction('training').objectStore('training').get('prepared-daily-queue');read.onsuccess=()=>{resolve({attempts:read.result?.attempts?.length??0,cards:read.result?.cards?.length??0});db.close();};};});
      };

export const readFirstPreparedAttempt = () => new Promise(resolve => {
        const registry=JSON.parse(localStorage.getItem('tempo-learning-authorities-v1'));
        const opening=indexedDB.open(Object.values(registry)[0].offlineDatabase);
        opening.onsuccess=()=>{const db=opening.result;const read=db.transaction('training').objectStore('training').get('prepared-daily-queue');read.onsuccess=()=>{resolve(read.result.attempts[0]);db.close();};};
      });

export const observePreparedAcknowledgementsDuringBootstrap = endpoint=>{
      const nativePut=IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put=function(value,...keys) {
        const request=nativePut.call(this,value,...keys);
        if (this.name==='training' && keys[0]==='prepared-daily-queue') {
          const acknowledged=structuredClone(value?.attempts?.filter(attempt=>attempt.serverAcknowledged)??[]);
          if (acknowledged.length) this.transaction.addEventListener('complete',()=>{
            void window.__tempoObserveOfflineAcknowledgement(acknowledged);
          },{once:true});
        }
        return request;
      };
      const nativeFetch=window.fetch;
      window.fetch=async(...argumentsList)=>{
        const response=await nativeFetch(...argumentsList);
        const input=argumentsList[0];
        const url=new URL(input instanceof Request?input.url:String(input),location.href);
        if (url.origin===endpoint && url.pathname==='/api/study/bootstrap' && response.status===200) {
          void window.__tempoObserveBootstrap({phase:'headers',status:response.status});
          for (const method of ['text','json']) {
            const nativeRead=response[method].bind(response);
            response[method]=(...readArguments)=>{
              const reading=nativeRead(...readArguments);
              void window.__tempoObserveBootstrap({phase:'body-start',status:response.status});
              return reading.catch(cause=>{
                void window.__tempoObserveBootstrap({phase:'body-failure',status:response.status,name:cause.name});
                throw cause;
              });
            };
          }
        }
        return response;
      };
    };
