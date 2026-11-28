// Independent native-MMD input diagnostic; used on the supplied, unchanged PMX.
float4x4 WVP : WORLDVIEWPROJECTION;
float Power : SPECULARPOWER < string Object = "Geometry"; >;
float4 VS(float4 position : POSITION) : POSITION { return mul(position, WVP); }
float4 PS() : COLOR0 {
    return float4(Power / 10.0, pow(0.5, Power * 3.0), pow(0.0, Power), 1.0);
}
technique Test < string MMDPass = "object"; > {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
