// The Gateway's WebSocket protocol (docs/design.md Section 7), as types.
// Everything that talks to the Gateway goes through these, so a protocol
// change is a compile error here instead of a silent runtime mismatch.

/** Client -> Gateway control messages. Audio goes as raw binary frames
 *  (16-bit / 16 kHz / mono PCM). */
export type ClientMessage =
  | { type: 'select_persona'; id: string }
  | { type: 'talk_start' }
  | { type: 'talk_end' }
  | { type: 'cancel' };

export type ServerState = 'listening' | 'thinking' | 'speaking' | 'done' | 'error';

export interface AudioFormat {
  sample_rate: number;
  bits: number;
  channels: number;
}

/** Gateway -> client JSON messages. TTS audio follows `audio_header` as
 *  binary frames, until `done`. */
export type ServerMessage =
  | { type: 'status'; state: ServerState; detail?: string | null }
  | { type: 'transcript'; text: string }
  | { type: 'llm_text'; text: string }
  | ({ type: 'audio_header' } & AudioFormat)
  | { type: 'stop_audio' }
  | { type: 'done' };

/** A persona as returned by GET /profiles (discovered from Open WebUI). */
export interface Persona {
  id: string;
  display_name: string;
  /** data: URI, or null when none is set in Open WebUI. */
  avatar: string | null;
}

export const PCM_INPUT_RATE = 16000;
