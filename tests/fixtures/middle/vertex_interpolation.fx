// Independent interpolation diagnostic, run on the supplied real PMX mesh.
float4x4 WVP : WORLDVIEWPROJECTION;
float Time : TIME;
float3 LightRay : DIRECTION < string Object = "Light"; >;
float Shift : CONTROLOBJECT < string name = "Vertex Control"; string item = "X"; >;
float Gain < string UIName = "Vertex gain"; > = 0.8;
struct Output {
    float4 position : POSITION;
    float2 uv : TEXCOORD0;
    float value : TEXCOORD1;
    float spatial : TEXCOORD2;
};
Output VS(float4 position : POSITION, float2 uv : TEXCOORD0) {
    Output result;
    result.position = mul(position, WVP);
    result.uv = uv;
    result.value = uv.x * uv.x * Gain + Shift * 0.1 + Time * 0.01 + LightRay.x * 0.02;
    result.spatial = position.x * position.x - 25.0;
    return result;
}
float4 PS(Output input) : COLOR {
    return float4(input.value * 0.5, Gain * 0.25, input.spatial * 0.01, 1);
}
technique Material < string MMDPass = "object"; > {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
