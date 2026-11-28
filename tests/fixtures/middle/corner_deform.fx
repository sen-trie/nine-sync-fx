// Independent numerical diagnostic; exercised on the real supplied Sanae PMX.
float4x4 WVP : WORLDVIEWPROJECTION;
float Time : TIME;
float Gain < string UIName = "Displacement"; > = 0.4;
struct Output {
    float4 position : POSITION;
    float3 normal : TEXCOORD0;
    float2 uv : TEXCOORD1;
};
Output VS(float4 position : POSITION, float3 normal : NORMAL, float2 uv : TEXCOORD0) {
    Output result;
    position.x += uv.x * uv.x * Gain + Time * 0.1;
    result.position = mul(position, WVP);
    result.normal = normal;
    result.uv = uv;
    return result;
}
float4 PS(Output input) : COLOR {
    return float4(input.normal * 0.25 + 0.5, 1);
}
technique Model < string MMDPass = "object"; > {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
