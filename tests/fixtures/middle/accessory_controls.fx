// Independent diagnostic for real accessory controllers on a real PMX destination.
float4x4 WVP : WORLDVIEWPROJECTION;
float4x4 Transform : CONTROLOBJECT < string name = "PointLight_1x.x"; >;
float3 Rotation : CONTROLOBJECT < string name = "PointLight_1x.x"; string item = "Rxyz"; >;
float Size : CONTROLOBJECT < string name = "PointLight_1x.x"; string item = "Si"; >;
float Opacity : CONTROLOBJECT < string name = "PointLight_1x.x"; string item = "Tr"; >;
float4 Offset : CONTROLOBJECT < string name = "PointLight_1x.x"; >;
float Scale : CONTROLOBJECT < string name = "PointLight_1x.x"; >;
bool Present : CONTROLOBJECT < string name = "PointLight_1x.x"; >;
bool Optional : CONTROLOBJECT < string name = "Optional-controller.pmx"; >;
float MissingMorph : CONTROLOBJECT < string name = "Optional-controller.pmx"; string item = "Gain"; >;
struct Output {
    float4 position : POSITION;
    float4 transformed : TEXCOORD0;
    float4 rotation_size : TEXCOORD1;
    float4 offset_opacity : TEXCOORD2;
    float4 flags_scale : TEXCOORD3;
};
Output VS(float4 position : POSITION) {
    Output result;
    result.position = mul(position, WVP);
    result.transformed = mul(float4(0.1, 0.2, 0.3, 1), Transform);
    result.rotation_size = float4(Rotation, Size);
    result.offset_opacity = float4(Offset.xyz, Opacity);
    result.flags_scale = float4(Present, Scale, Optional ? MissingMorph : 0, 1);
    return result;
}
float4 PS(Output input) : COLOR {
    return (input.transformed + input.rotation_size + input.offset_opacity + input.flags_scale) * 0.01;
}
technique Model < string MMDPass = "object"; > {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
