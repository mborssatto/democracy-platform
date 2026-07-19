<script setup lang="ts">
defineEmits<{ login: [username: string] }>()
</script>

<template>
  <div class="card" style="max-width:520px;margin:40px auto">
    <h2>🔐 Log in</h2>
    <p class="hint">
      Dev stub: pick a demo user — no passwords exist in this prototype.
      Production replaces this with Supabase Auth (real accounts + JWTs).
      Responders only see the Respond flow, scoped to their own claims.
    </p>
    <div class="row">
      <button class="btn btn-blue" @click="$emit('login', 'admin')">Enter as admin</button>
      <button class="btn btn-teal" @click="$emit('login', 'staff')">Enter as staff</button>
      <button class="btn btn-ghost" @click="$emit('login', 'responder')">Enter as responder</button>
    </div>
  </div>
</template>
