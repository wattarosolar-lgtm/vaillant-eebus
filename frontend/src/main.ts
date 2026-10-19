import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import './app.css';

import { mount } from 'svelte';

import App from './App.svelte';

const target = document.getElementById('app');
if (!target) {
  throw new Error('Element #app fehlt in index.html');
}

export default mount(App, { target });
