//! RFC 8785 (JSON Canonicalization Scheme) — med tillägget `ben3d-num-as-string/1`
//! (D2): alla uppmätta/exakta numeriska värden (koordinater, tider, hastigheter,
//! stampar) serialiseras som STRÄNGAR med extraktorns exakta token, så flyttal aldrig
//! normaliseras. Heltalsräknare (seq/antal/cell-/länk-id) får vara JSON-tal.
//!
//! Detta modul hanterar därför bara null/bool/heltal/sträng/array/objekt. Nycklar
//! sorteras lexikografiskt (ASCII ⇒ byteordning == UTF-16-kodenhetsordning), inga
//! mellanslag, strängar escape:as minimalt per RFC 8785 (§3.2.2.2).

use serde_json::Value;

pub fn canonical(v: &Value) -> String {
    let mut out = String::new();
    write_canonical(v, &mut out);
    out
}

fn write_canonical(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            // RFC 8785 §3.2.2.3: heltal som decimal. f64 får inte förekomma —
            // uppmätta värden ska redan vara strängar (ben3d-num-as-string/1).
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string());
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string());
            } else {
                panic!("canonical: f64 förekommer — använd ben3d-num-as-string/1");
            }
        }
        Value::String(s) => write_string(s, out),
        Value::Array(a) => {
            out.push('[');
            for (i, e) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_canonical(e, out);
            }
            out.push(']');
        }
        Value::Object(m) => {
            out.push('{');
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort_unstable();
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_string(k, out);
                out.push(':');
                write_canonical(&m[*k], out);
            }
            out.push('}');
        }
    }
}

fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{09}' => out.push_str("\\t"),
            '\u{0a}' => out.push_str("\\n"),
            '\u{0c}' => out.push_str("\\f"),
            '\u{0d}' => out.push_str("\\r"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rfc8785_sorts_keys_and_escapes() {
        let v = json!({"b": 1, "a": "x\"y", "c": [true, null, "n\n"]});
        let c = canonical(&v);
        assert_eq!(c, r#"{"a":"x\"y","b":1,"c":[true,null,"n\n"]}"#);
    }

    #[test]
    fn floats_are_rejected() {
        let v = json!({"a": 1.5});
        let r = std::panic::catch_unwind(|| canonical(&v));
        assert!(r.is_err(), "f64 ska vägra — num-as-string gäller");
    }
}
