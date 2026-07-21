<script setup lang="ts">
withDefaults(defineProps<{ label?: string }>(), { label: 'Loading…' })
</script>

<template>
  <div class="loading"><span class="spinner" /><span>{{ label }}</span></div>
</template>
