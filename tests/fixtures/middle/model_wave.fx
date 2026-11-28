float4x4 WVP : WORLDVIEWPROJECTION;
float Time : TIME < bool SyncInEditMode = true; >;
float Amplitude = 0.2;
float Frequency = 1.25;
float ControllerX : CONTROLOBJECT < string name = "MME Control"; string item = "X"; >;
float3 Bone : CONTROLOBJECT < string name = "Demo Rig"; string item = "Signal"; >;
float Morph : CONTROLOBJECT < string name = "(self)"; string item = "Lift"; >;

struct Result {
    float4 clip : POSITION0;
    float3 local : TEXCOORD7;
};

Result VS(float4 position : POSITION0) {
    Result o;
    float3 p = position.xyz;
    p.y += Amplitude * ControllerX * (1 + Bone.y + Morph) *
           sin((p.x - ControllerX) * Frequency + Time);
    o.local = p;
    o.clip = mul(float4(p, 1.0), WVP);
    return o;
}

float4 PS() : COLOR0 {
    return float4(0.25, 0.5, 0.7, 1.0);
}

technique Model < string MMDPass = "object"; string NineSyncLocalPosition = "TEXCOORD7"; > {
    pass SurfacePass {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
