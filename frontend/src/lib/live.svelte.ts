/**
 * Marks from the backend's event stream (1.4.2): `rooms` counts the events "rooms" - something on the page
 * "Räume" changed (thermostat, valve, room sensor, probe, a decision or a command). The page reloads on them.
 */
export const live = $state({ rooms: 0 });
