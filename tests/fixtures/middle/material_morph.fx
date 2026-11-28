// Independent numeric probe. Real ToonDX conversion is tested separately.
float4x4 WVP : WORLDVIEWPROJECTION;
float4 D : DIFFUSE < string Object="Geometry"; >;
float3 A : AMBIENT < string Object="Geometry"; >;
float3 E : EMISSIVE < string Object="Geometry"; >;
float3 S : SPECULAR < string Object="Geometry"; >;
float P : SPECULARPOWER < string Object="Geometry"; >;
float4 Edge : EDGECOLOR < string Object="Geometry"; >;
float GroupWeight : CONTROLOBJECT < string name="(self)"; string item="FX Tint Group"; >;
struct Output {
    float4 position:POSITION;
    float4 diffuse:TEXCOORD0;
    float3 ambient:TEXCOORD1;
    float3 emissive:TEXCOORD2;
    float4 specular_power:TEXCOORD3;
    float4 edge:TEXCOORD4;
};
Output VS(float4 position:POSITION) {
    Output o;
    o.position=mul(position,WVP);
    o.diffuse=D; o.ambient=A; o.emissive=E;
    o.specular_power=float4(S,P); o.edge=Edge;
    return o;
}
float4 PS(Output i):COLOR {
    float3 rgb=(i.diffuse.rgb+D.rgb+i.ambient+A+i.emissive+E+i.specular_power.rgb+S)*0.1;
    rgb+=i.edge.rgb*0.1+(i.specular_power.w+P)*0.001+(i.diffuse.a+D.a)*0.02+i.edge.a*0.03;
    return float4(rgb+GroupWeight*0.01,1);
}
technique Model < string MMDPass="object"; > {
    pass Main {
        VertexShader=compile vs_3_0 VS();
        PixelShader=compile ps_3_0 PS();
    }
}
