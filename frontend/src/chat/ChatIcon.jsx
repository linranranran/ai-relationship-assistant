const paths = {
  sparkle: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
  chat: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z',
  people: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.9M15 3.1a4 4 0 0 1 0 7.8M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  person: 'M20 21v-2a7 7 0 0 0-14 0v2M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  relations: 'M12 8v4M5 16v-4h14v4M9 2h6v6H9V2ZM2 16h6v6H2v-6ZM16 16h6v6h-6v-6Z',
  send: 'm22 2-7 20-4-9-9-4L22 2ZM22 2 11 13',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  history: 'M3 11a9 9 0 1 1 2.7 7M3 3v8h8M12 7v5l3 2',
  arrow: 'M12 5v14M5 12l7 7 7-7',
  check: 'm5 12 4 4L19 6',
  copy: 'M9 9h12v12H9V9ZM5 15H3V3h12v2',
  alert: 'M12 8v5M12 17h.01M10.3 3.8 1.8 18.5A1 1 0 0 0 2.7 20h18.6a1 1 0 0 0 .9-1.5L13.7 3.8a2 2 0 0 0-3.4 0Z',
}

export default function ChatIcon({ name = 'sparkle', size = 20, ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" {...props}><path d={paths[name] || paths.sparkle} /></svg>
}
