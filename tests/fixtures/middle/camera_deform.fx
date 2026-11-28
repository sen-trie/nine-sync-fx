// Independent scene-input regression on the supplied multi-material PMX.
float4x4 WVP : WORLDVIEWPROJECTION;
float3 CameraPosition : POSITION < string Object = "Camera"; >;
float3 LightRay : DIRECTION < string Object = "Light"; >;
float4 VS(float4 position : POSITION) : POSITION {
    position.x += CameraPosition.x * 0.02;
    position.y += LightRay.y * 0.03;
    return mul(position, WVP);
}
float4 PS() : COLOR { return 1; }
technique Model < string MMDPass = "object"; > {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
