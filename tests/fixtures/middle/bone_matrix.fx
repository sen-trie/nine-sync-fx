float4x4 WVP : WORLDVIEWPROJECTION;
float4x4 Bone : CONTROLOBJECT < string name="(self)"; string item="ProbeArm"; >;
float3 Center : CONTROLOBJECT < string name="(self)"; string item="ProbeCenter"; >;
float4x4 Other : CONTROLOBJECT < string name="Bone controller.pmx"; string item="ProbeArm"; >;
struct Output {
    float4 position:POSITION;
    float4 row0:TEXCOORD0;
    float4 row1:TEXCOORD1;
    float4 row2:TEXCOORD2;
    float4 row3:TEXCOORD3;
    float3 center:TEXCOORD4;
    float4 other:TEXCOORD5;
};
Output VS(float4 p:POSITION) {
    Output o;
    o.position=mul(p,WVP);
    o.row0=Bone[0]; o.row1=Bone[1]; o.row2=Bone[2]; o.row3=Bone[3];
    o.center=Center; o.other=Other[3];
    return o;
}
float4 PS(Output o):COLOR {
    // Exercise independent pixel-stage bindings as well as vertex varyings.
    return float4(dot(o.row0+o.row1,float4(1,2,3,4))*0.02+Bone[1][1]*0.1+0.3,
                  dot(o.row2+o.row3,float4(1,2,3,4))*0.005+Bone[2][2]*0.01+0.3,
                  dot(o.center,float3(1,2,3))*0.005+dot(o.other,float4(1,2,3,4))*0.005
                    +Center.y*0.01+Other[3][2]*0.01+0.2,1);
}
technique Model < string MMDPass="object"; > {
    pass Main {
        VertexShader=compile vs_3_0 VS();
        PixelShader=compile ps_3_0 PS();
    }
}
